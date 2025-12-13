# -*- coding: utf-8 -*-
"""
网页内容递归阅读器 - 配置文件
"""

# 爬虫配置
DEFAULT_CONFIG = {
    # 最大递归深度 (0 = 仅当前页面, 1 = 当前页面 + 链接页面, 以此类推)
    "max_depth": 1,
    
    # 最大抓取页面数量
    "max_pages": 50,
    
    # 请求超时时间 (秒)
    "timeout": 30,
    
    # 请求间隔时间 (秒) - 避免过于频繁的请求
    "delay": 1.0,
    
    # 是否只抓取同域名下的链接
    "same_domain_only": True,

    # Playwright 配置
    "headless": False,       # 方便调试：默认显示浏览器
    "concurrency": 2,        # 降低并发，模拟人类行为
    "wait_until": "domcontentloaded", # 降低标准，防止网络空闲检测超时
    "js_render_wait": 5.0,   # 增加等待时间，确保飞书完全渲染
    
    # ISO-8601 要排除的URL模式 (正则表达式)
    "exclude_patterns": [
        r".*\.(jpg|jpeg|png|gif|bmp|svg|ico)$",
        r".*\.(css|js|woff|woff2|ttf|eot)$",
        r".*\.(pdf|doc|docx|xls|xlsx|ppt|pptx)$",
        r".*\.(zip|rar|7z|tar|gz)$",
        r".*\.(mp3|mp4|avi|mkv|mov|wmv)$",
        r".*/login.*",
        r".*/logout.*",
        r".*/register.*",
    ],
    
    # 输出配置
    "output_dir": "./output",
    "output_format": "markdown",  # markdown, json, txt
    
    "extract_settings": {
        "remove_scripts": True,
        "remove_styles": True,
        "remove_comments": True,
        "min_text_length": 10,  # 降低最小文本要求，防止因提取不全被过滤
    }
}
