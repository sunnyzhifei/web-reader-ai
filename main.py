#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网页内容递归阅读器 - 主程序入口

用法:
    python main.py <URL> [选项]

示例:
    python main.py https://example.com
    python main.py https://example.com --depth 3 --max-pages 100
    python main.py https://example.com --format json --output ./my_output
"""

import argparse
import asyncio
import sys
import os
import io

# 强制设置标准输出为 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


# 将当前目录添加到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler import WebReader
from config import DEFAULT_CONFIG


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='网页内容递归阅读器 - 递归抓取网页内容并保存',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s https://example.com
  %(prog)s https://example.com --depth 2 --max-pages 50
  %(prog)s https://blog.example.com --format markdown --same-domain
        """
    )
    
    parser.add_argument(
        'url',
        help='起始URL (必须包含 http:// 或 https://)'
    )
    
    parser.add_argument(
        '-d', '--depth',
        type=int,
        default=DEFAULT_CONFIG['max_depth'],
        help=f'最大递归深度 (默认: {DEFAULT_CONFIG["max_depth"]})'
    )
    
    parser.add_argument(
        '-m', '--max-pages',
        type=int,
        default=DEFAULT_CONFIG['max_pages'],
        help=f'最大抓取页面数 (默认: {DEFAULT_CONFIG["max_pages"]})'
    )
    
    parser.add_argument(
        '-f', '--format',
        choices=['markdown', 'json', 'txt'],
        default=DEFAULT_CONFIG['output_format'],
        help=f'输出格式 (默认: {DEFAULT_CONFIG["output_format"]})'
    )
    
    parser.add_argument(
        '-o', '--output',
        default=DEFAULT_CONFIG['output_dir'],
        help=f'输出目录 (默认: {DEFAULT_CONFIG["output_dir"]})'
    )
    
    parser.add_argument(
        '--delay',
        type=float,
        default=DEFAULT_CONFIG['delay'],
        help=f'请求间隔秒数 (默认: {DEFAULT_CONFIG["delay"]})'
    )
    
    parser.add_argument(
        '--timeout',
        type=int,
        default=DEFAULT_CONFIG['timeout'],
        help=f'请求超时秒数 (默认: {DEFAULT_CONFIG["timeout"]})'
    )
    
    parser.add_argument(
        '--no-same-domain',
        action='store_true',
        help='允许抓取跨域链接 (默认: 仅同域名)'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='显示详细输出'
    )
    
    return parser.parse_args()


def validate_url(url: str) -> bool:
    """验证URL格式"""
    if not url.startswith(('http://', 'https://')):
        print(f"❌ 错误: URL必须以 http:// 或 https:// 开头")
        print(f"   您输入的是: {url}")
        return False
    return True


async def main():
    """主函数"""
    args = parse_args()
    
    # 验证URL
    if not validate_url(args.url):
        sys.exit(1)
    
    # 构建配置
    config = {
        'max_depth': args.depth,
        'max_pages': args.max_pages,
        'output_format': args.format,
        'output_dir': args.output,
        'delay': args.delay,
        'timeout': args.timeout,
        'same_domain_only': not args.no_same_domain,
    }
    
    # 显示配置信息
    print("\n" + "=" * 60)
    print("📖 网页内容递归阅读器")
    print("=" * 60)
    print(f"🌐 目标URL: {args.url}")
    print(f"📊 配置:")
    print(f"   - 最大深度: {config['max_depth']}")
    print(f"   - 最大页面数: {config['max_pages']}")
    print(f"   - 输出格式: {config['output_format']}")
    print(f"   - 输出目录: {config['output_dir']}")
    print(f"   - 请求间隔: {config['delay']}秒")
    print(f"   - 仅同域名: {'是' if config['same_domain_only'] else '否'}")
    print("=" * 60)
    
    # 创建爬虫并开始抓取
    reader = WebReader(config)
    
    try:
        await reader.crawl(args.url)
        reader.save_results()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断，正在保存已抓取的内容...")
        if reader.results:
            reader.save_results()
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    
    print("\n👋 感谢使用!\n")


if __name__ == '__main__':
    # Windows 下通常不需要手动设置 EventLoopPolicy，Python 3.8+ 默认使用 ProactorEventLoop
    # 如果遇到 "NotImplementedError"，请确保使用的是 ProactorEventLoop
    # if sys.platform == 'win32':
    #     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())
