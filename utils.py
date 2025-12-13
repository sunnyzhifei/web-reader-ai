# -*- coding: utf-8 -*-
"""
网页内容递归阅读器 - 工具函数模块
"""

import re
import os
import json
from urllib.parse import urlparse, urljoin
from datetime import datetime
from typing import Optional, List, Set


def normalize_url(url: str, base_url: str = None) -> Optional[str]:
    """
    标准化URL，处理相对路径和绝对路径
    
    Args:
        url: 待处理的URL
        base_url: 基础URL (用于解析相对路径)
    
    Returns:
        标准化后的URL，无效则返回None
    """
    if not url or url.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
        return None
    
    # 处理相对路径
    if base_url and not url.startswith(('http://', 'https://')):
        url = urljoin(base_url, url)
    
    # 移除锚点
    url = url.split('#')[0]
    
    # 移除尾部斜杠（统一格式）
    url = url.rstrip('/')
    
    return url if url.startswith(('http://', 'https://')) else None


def get_domain(url: str) -> str:
    """
    从URL中提取域名
    
    Args:
        url: 完整URL
    
    Returns:
        域名字符串
    """
    parsed = urlparse(url)
    return parsed.netloc


def is_same_domain(url1: str, url2: str) -> bool:
    """
    检查两个URL是否属于同一域名
    
    Args:
        url1: 第一个URL
        url2: 第二个URL
    
    Returns:
        是否同域名
    """
    return get_domain(url1) == get_domain(url2)


def should_exclude_url(url: str, patterns: List[str]) -> bool:
    """
    检查URL是否应该被排除
    
    Args:
        url: 待检查的URL
        patterns: 排除模式列表 (正则表达式)
    
    Returns:
        是否应该排除
    """
    url_lower = url.lower()
    for pattern in patterns:
        if re.search(pattern, url_lower, re.IGNORECASE):
            return True
    return False


def sanitize_filename(text: str, max_length: int = 100) -> str:
    """
    将文本转换为安全的文件名
    
    Args:
        text: 原始文本
        max_length: 最大长度
    
    Returns:
        安全的文件名
    """
    # 移除非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '', text)
    # 替换空白为下划线
    filename = re.sub(r'\s+', '_', filename)
    # 限制长度
    filename = filename[:max_length]
    # 移除首尾的点和空格
    filename = filename.strip('. ')
    
    return filename or 'untitled'


def create_output_dir(base_dir: str) -> str:
    """
    创建带时间戳的输出目录
    
    Args:
        base_dir: 基础目录路径
    
    Returns:
        创建的目录路径
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = os.path.join(base_dir, f'crawl_{timestamp}')
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def save_content(content: dict, filepath: str, format: str = 'markdown'):
    """
    保存内容到文件
    
    Args:
        content: 内容字典 (包含 title, url, text 等)
        filepath: 文件路径 (不含扩展名)
        format: 输出格式 (markdown, json, txt)
    """
    if format == 'markdown':
        ext = '.md'
        text = f"# {content.get('title', 'Untitled')}\n\n"
        text += f"> **URL:** {content.get('url', '')}\n"
        text += f"> **抓取时间:** {content.get('crawl_time', '')}\n\n"
        text += "---\n\n"
        text += content.get('text', '')
    elif format == 'json':
        ext = '.json'
        text = json.dumps(content, ensure_ascii=False, indent=2)
    else:
        ext = '.txt'
        text = f"标题: {content.get('title', 'Untitled')}\n"
        text += f"URL: {content.get('url', '')}\n"
        text += f"抓取时间: {content.get('crawl_time', '')}\n"
        text += "=" * 50 + "\n\n"
        text += content.get('text', '')
    
    with open(filepath + ext, 'w', encoding='utf-8') as f:
        f.write(text)


def print_progress(current: int, total: int, url: str, depth: int):
    """
    打印进度信息
    
    Args:
        current: 当前进度
        total: 总数
        url: 当前URL
        depth: 当前深度
    """
    progress = f"[{current}/{total}]" if total > 0 else f"[{current}]"
    depth_indicator = "  " * depth + "└─" if depth > 0 else ""
    
    # 截断过长的URL
    display_url = url if len(url) <= 60 else url[:57] + "..."
    
    print(f"{progress} 深度{depth} {depth_indicator} {display_url}")
