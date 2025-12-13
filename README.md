# 网页内容递归阅读器 (Web Reader)

一个强大的 Python 网页内容递归抓取工具，支持异步抓取、智能内容提取和多种输出格式。

## ✨ 功能特性

- 🔄 **递归抓取** - 自动递归抓取网页中的链接
- ⚡ **异步高效** - 基于 aiohttp 的异步抓取，速度更快
- 🧹 **智能提取** - 自动识别正文区域，过滤导航/广告等噪音
- 📝 **多种格式** - 支持 Markdown、JSON、TXT 输出
- 🔒 **安全限制** - 支持同域名限制、深度限制、页面数量限制
- 🎯 **灵活配置** - 命令行参数 + 配置文件双重配置

## 🚀 快速开始

### 安装依赖

```bash
cd web_reader
pip install -r requirements.txt
playwright install chromium
```

### 基本用法

```bash
# 抓取单个网站
python main.py https://example.com

# 指定深度和页面数
python main.py https://docs.python.org --depth 2 --max-pages 50

# 输出为 JSON 格式
python main.py https://blog.example.com --format json

# 指定输出目录
python main.py https://example.com --output ./my_crawl_results
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | 起始URL (必填) | - |
| `-d, --depth` | 最大递归深度 | 2 |
| `-m, --max-pages` | 最大抓取页面数 | 50 |
| `-f, --format` | 输出格式 (markdown/json/txt) | markdown |
| `-o, --output` | 输出目录 | ./output |
| `--delay` | 请求间隔(秒) | 1.0 |
| `--timeout` | 请求超时(秒) | 30 |
| `--no-same-domain` | 允许跨域抓取 | 仅同域名 |
| `-v, --verbose` | 详细输出 | 否 |

## 📁 项目结构

```
web_reader/
├── main.py           # 主程序入口
├── crawler.py        # 核心爬虫类
├── config.py         # 配置文件
├── utils.py          # 工具函数
├── requirements.txt  # 依赖列表
└── README.md         # 说明文档
```

## ⚙️ 配置说明

编辑 `config.py` 可以修改默认配置：

```python
DEFAULT_CONFIG = {
    "max_depth": 2,           # 递归深度
    "max_pages": 50,          # 最大页面数
    "timeout": 30,            # 请求超时
    "delay": 1.0,             # 请求间隔
    "same_domain_only": True, # 仅同域名
    "output_format": "markdown",
    "exclude_patterns": [...] # URL排除规则
}
```

## 📤 输出示例

抓取完成后，会在输出目录生成：

```
output/crawl_20231213_143022/
├── index.md              # 索引文件
├── 001_首页.md
├── 002_关于我们.md
├── 003_产品介绍.md
└── ...
```

## ⚠️ 注意事项

1. 请遵守目标网站的 robots.txt 规则
2. 适当设置请求间隔，避免对服务器造成压力
3. 抓取内容仅供个人学习使用

## 📄 License

MIT License
