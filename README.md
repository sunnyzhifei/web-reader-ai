# 🕸️ Web Reader AI

> **智能递归网页采集与 Markdown 转换工具**  
> 基于 Playwright & FastAPI，专为构建 AI 知识库设计。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)

Web Reader AI 是一个现代化的网页采集工具，它不仅能递归抓取网页内容，还能将其转换为高质量的 Markdown 文档。特别针对 **飞书云文档 (Feishu/Lark)** 进行了深度优化，解决了动态加载、嵌套链接和子域名重复抓取等痛点。

---

## ✨ 核心特性

- **🖥️ 现代化 Web 界面** - 提供直观的 Web UI，支持实时进度监控和操作流程引导。
- **🔄 智能递归抓取** - 自动发现并递归抓取页面链接，支持深度和数量限制。
- **📝 高质量 Markdown** - 将 HTML 智能转换为 Markdown，保留标题结构、代码块和表格。
- **🔗 离线链接修复** - 自动将网页中的超链接替换为本地生成的 Markdown 文件链接，构建完整的离线知识库。
- **👁️ 智能预览** - 支持抓取前预览，在左侧目录树和右侧 Markdown 预览区查看效果，所见即所得。
- **🦅 飞书/Lark 深度优化**：
    - 自动处理飞书的动态渲染内容。
    - 智能去重策略（识别不同子域名下的同一文档）。
    - 完美修复嵌套链接（双重链接）问题。

## 🚀 快速开始

### 1. 安装依赖

确保你已经安装了 Python 3.8+。

```bash
# 1. 克隆仓库
git clone https://github.com/sunnyzhifei/web-reader-ai.git
cd web-reader-ai

# 2. 创建并激活虚拟环境 (可选)
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 安装浏览器驱动
playwright install chromium
```

### 2. 启动服务

运行以下命令启动 Web 服务器：

```bash
python server.py
# 或者使用 uvicorn:
# uvicorn server:app --reload
```

启动后，浏览器访问 **http://127.0.0.1:8000** 即可使用。

## 📖 使用指南

1.  **预览内容 (Preview)**
    - 输入起始 URL（如飞书知识库首页）。
    - 此步骤只抓取少量页面（默认 5 页），用于快速确认抓取效果。
    - 在右侧预览区查看目录树和 Markdown 渲染结果。

2.  **开始抓取 (Start Crawl)**
    - 确认预览无误后，点击"开始抓取"。
    - 系统将后台执行完整的递归抓取任务。
    - 进度条实时显示抓取状态。

3.  **下载结果 (Download)**
    - 任务完成后，点击"下载结果"。
    - 自动打包所有 Markdown 文件为 ZIP 压缩包。
    - 解压后即可获得完整的本地知识库（支持 Obsidian 等笔记软件）。

## ⚙️ 高级配置

你可以在界面上调整以下参数：
- **最大深度**：递归抓取的层级深度（默认 1，即只抓取当前页及其直接子页）。
- **最大页面数**：限制总抓取页数，防止任务过大。

## 📁 项目结构

```
web_reader/
├── server.py           # FastAPI 后端服务
├── crawler.py          # 核心爬虫逻辑 (Playwright + BeautifulSoup)
├── static/             # 前端静态资源
│   ├── index.html      # 主界面
│   ├── app.js          # 前端逻辑
│   └── style.css       # 样式文件 (Glassmorphism 风格)
├── output/             # 抓取结果输出目录
├── requirements.txt    # 依赖列表
└── README.md           # 说明文档
```

## ⚠️ 注意事项

1.  本工具使用了无头浏览器 (Playwright)，初次运行会自动下载 Chromium。
2.  请遵守目标网站的 `robots.txt` 规则，不要对服务器造成过大压力。
3.  抓取内容仅供个人学习和构建私有知识库使用。

## 📄 License

MIT License
