# -*- coding: utf-8 -*-
import sys
import asyncio
import threading
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import shutil
import uuid
import os
from typing import Dict, Any
from crawler import WebReader

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_root():
    return FileResponse("static/index.html")

tasks: Dict[str, Dict[str, Any]] = {}

class CrawlRequest(BaseModel):
    url: str
    max_depth: int = 1
    max_pages: int = 50

# 实际的异步抓取逻辑
async def _crawl_logic(task_id: str, req: CrawlRequest, is_preview: bool):
    try:
        config = {
            "max_depth": req.max_depth,
            "max_pages": min(req.max_pages, 3) if is_preview else req.max_pages,
            "headless": True,
            "output_dir": f"output/{task_id}"
        }
        
        reader = WebReader(config)
        
        async def on_progress(current, total, url, depth):
            tasks[task_id]["progress"] = {
                "current": current,
                "total": total,
                "url": url,
                "depth": depth
            }
            
        await reader.crawl(req.url, on_progress=on_progress)
        
        if is_preview:
            ordered_data = reader.get_ordered_results()
            preview_list = []
            for item in ordered_data:
                preview_list.append({
                    "title": item.get("title", "No Title"),
                    "url": item.get("url", ""),
                    "text_preview": item.get("text", "")[:300] + "..." if item.get("text") else ""
                })
            tasks[task_id]["preview_data"] = preview_list
            tasks[task_id]["status"] = "completed"
        else:
            output_dir = f"output/{task_id}"
            reader.save_results(output_dir)
            tasks[task_id]["result_dir"] = output_dir
            tasks[task_id]["status"] = "completed"
            
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        import traceback
        traceback.print_exc()

# 线程包装器：为 Windows 设置 Proactor 并运行异步循环
def run_crawl_thread(task_id: str, req: CrawlRequest, is_preview: bool):
    print(f"DEBUG: Thread started for {task_id}")
    import traceback
    
    if sys.platform == 'win32':
        print("DEBUG: Setting WindowsProactorEventLoopPolicy")
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    try:
        print("DEBUG: Entering asyncio.run")
        asyncio.run(_crawl_logic(task_id, req, is_preview))
        print("DEBUG: asyncio.run completed")
    except Exception as e:
        print(f"THREAD ERROR: {repr(e)}")
        traceback.print_exc()
        try:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e) or repr(e)
        except:
            pass

@app.post("/api/preview")
async def start_preview(req: CrawlRequest):
    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "running", 
        "progress": {"current": 0, "total": 5},
        "preview_data": None
    }
    # 使用 Thread 隔离运行环境，避免 Event Loop 冲突
    threading.Thread(target=run_crawl_thread, args=(task_id, req, True)).start()
    return {"task_id": task_id}

@app.post("/api/crawl")
async def start_crawl(req: CrawlRequest):
    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "running", 
        "progress": {"current": 0, "total": req.max_pages},
        "result_dir": None
    }
    threading.Thread(target=run_crawl_thread, args=(task_id, req, False)).start()
    return {"task_id": task_id}

@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]

@app.get("/api/download/{task_id}")
async def download_results(task_id: str):
    if task_id not in tasks or tasks[task_id].get("status") != "completed":
        raise HTTPException(status_code=400, detail="Task not ready or failed")
    
    dir_path = tasks[task_id].get("result_dir")
    
    if not dir_path or not os.path.exists(dir_path):
        raise HTTPException(status_code=404, detail="Result files not found")
    
    zip_filename = f"output/{task_id}" 
    if not os.path.exists(zip_filename + ".zip"):
         shutil.make_archive(zip_filename, 'zip', dir_path)
    
    return FileResponse(zip_filename + ".zip", filename=f"crawl_result_{task_id[:8]}.zip")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
