# -*- coding: utf-8 -*-
"""
网页内容递归阅读器 - 核心爬虫模块 (Playwright 版)
"""

import asyncio
import re
from bs4 import BeautifulSoup, Comment
from typing import Optional, Set, List, Dict, Any
from urllib.parse import urlparse
from datetime import datetime
from fake_useragent import UserAgent
from asyncio import Semaphore
from playwright.async_api import async_playwright, Page, BrowserContext

from config import DEFAULT_CONFIG
from utils import (
    normalize_url, 
    get_domain, 
    is_same_domain, 
    should_exclude_url,
    sanitize_filename,
    save_content,
    print_progress
)


class WebReader:
    """
    网页内容递归阅读器 (Playwright内核)
    
    支持异步递归抓取网页内容，使用无头浏览器渲染动态内容。
    """
    
    def __init__(self, config: dict = None):
        """
        初始化爬虫
        
        Args:
            config: 配置字典，覆盖默认配置
        """
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.visited_urls: Set[str] = set()
        self.results: List[Dict[str, Any]] = []
        self.ua = UserAgent()
        
    def _extract_text(self, html: str, url: str) -> Dict[str, Any]:
        """
        从HTML中提取正文内容 (使用BeautifulSoup清洗)
        
        Args:
            html: HTML内容
            url: 页面URL
        
        Returns:
            包含标题、文本、链接等的字典
        """
        # 注意: 即使使用了 Playwright，我们依然使用 BS4 进行文本清洗，
        # 因为它在处理 HTML 结构和去噪方面非常方便。
        soup = BeautifulSoup(html, 'lxml')
        settings = self.config['extract_settings']
        
        # 移除不需要的标签
        if settings['remove_scripts']:
            for script in soup.find_all('script'):
                script.decompose()
        
        if settings['remove_styles']:
            for style in soup.find_all('style'):
                style.decompose()
        
        if settings['remove_comments']:
            for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
                comment.extract()
        
        # 移除导航、页脚等非正文区域
        for tag in soup.find_all(['nav', 'footer', 'header', 'aside', 'iframe', 'noscript']):
            tag.decompose()
        
        # 提取标题
        title = ''
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)
        
        # 提取正文 - 优先查找主要内容区域
        main_content = None
        # 添加针对飞书 (.doc-body, .isv-doc-body) 和其他文档站点的选择器
        feishu_selectors = [
             '.doc-content', '.document-content', '.suite-wiki-content', 
             '.render-unit-wrapper', '.isv-doc-body', '.catalogue-content'
        ]
        common_selectors = ['main', 'article', '[role="main"]', '.content', '.main-content', '#content', '#main']
        
        for selector in feishu_selectors + common_selectors:
            main_content = soup.select_one(selector)
            if main_content:
                print(f"  [DEBUG] 找到内容容器: {selector}")
                break
        
        if not main_content:
            main_content = soup.find('body') or soup
        
        # --- 智能文本提取 (终极版 v2: 样式还原) ---
        text_parts = []
        
        # 1. 表格处理 (保持不变)
        for table in main_content.find_all('table'):
            rows = []
            for tr in table.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                rows.append('| ' + ' | '.join(cells) + ' |')
            if rows:
                if len(rows) > 1:
                    rows.insert(1, '| ' + ' | '.join(['---'] * len(rows[0].split('|')[1:-1])) + ' |')
                table_md = '\n'.join(rows) + '\n'
                table.replace_with(f"\n{table_md}\n")

        # 2. 辅助函数：处理行内元素，保留格式
        def process_node(node):
            if isinstance(node, str):
                return node
            
            # 忽略隐藏元素
            if node.name in ['style', 'script', 'noscript', 'iframe']:
                return ''
            
            content = ''
            for child in node.children:
                content += process_node(child)
            
            # 处理粗体/斜体/代码/链接
            if not content.strip():
                return ''
                
            if node.name in ['b', 'strong']:
                return f" **{content.strip()}** "
            if node.name in ['i', 'em']:
                return f" *{content.strip()}* "
            if node.name == 'code':
                return f" `{content.strip()}` "
            if node.name == 'a' and node.get('href'):
                href = node['href']
                if not href.startswith('javascript'):
                    return f" [{content.strip()}]({href}) "
                return content
            
            return content

        # 3. 遍历块级元素
        block_tags = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre', 'div', 'section'}
        
        for element in main_content.find_all(list(block_tags)):
            # 过滤掉包含其他块级元素的容器 (只处理最底层的块)
            has_block_children = any(child.name in block_tags for child in element.find_all(recursive=False))
            if has_block_children:
                continue
            
            # 使用 process_node 获取带格式的文本
            rich_text = process_node(element).strip()
            
            # 清洗多余空格 (将多个空格合并为一个，但保留 Markdown 标记间的空格)
            rich_text = re.sub(r'\s+', ' ', rich_text)
            rich_text = rich_text.replace(' **', '**').replace('** ', '**') # 修复粗体空格
            
            if len(rich_text) < 2:
                continue

            # --- 核心优化: 识别飞书伪标题 ---
            # 飞书常用 class="heading-h1" 或 "ace-line ace-line-heading-2"
            classes = element.get('class', [])
            class_str = ' '.join(classes).lower()
            
            # --- 噪音过滤 ---
            # 如果文本包含以下关键词，视为UI噪音直接丢弃
            blacklist = ["附件不支持打印", "文档链接直达", "评论区", "更多分类内容", "前往语雀", "扫码登录", "转到元文档"]
            if any(noise in rich_text for noise in blacklist):
                continue
            
            level = 0
            if element.name.startswith('h'):
                try: level = int(element.name[1])
                except: pass
            elif 'heading-h1' in class_str or 'ace-line-heading-1' in class_str: level = 1
            elif 'heading-h2' in class_str or 'ace-line-heading-2' in class_str: level = 2
            elif 'heading-h3' in class_str or 'ace-line-heading-3' in class_str: level = 3
            elif 'heading-h4' in class_str or 'ace-line-heading-4' in class_str: level = 4
            elif 'title' in class_str and len(rich_text) < 50: level = 2 # 可能是个标题
            
            # 组装 Markdown
            final_text = rich_text
            
            if level > 0:
                final_text = f"\n{'#' * level} {rich_text}\n"
            elif element.name == 'li' or 'list-item' in class_str:
                final_text = f"- {rich_text}"
            elif element.name == 'blockquote':
                final_text = f"> {rich_text}"
            elif element.name == 'pre':
                final_text = f"\n```\n{rich_text}\n```\n"
            
            # 只有当文本不重复且有意义时添加
            if final_text and (not text_parts or text_parts[-1].strip() != final_text.strip()):
                 text_parts.append(final_text)
            
        # 兜底
        if len(text_parts) < 3:
             print("  ⚠️  样式还原失败，回退到暴力提取...")
             text_parts = [main_content.get_text(separator='\n\n', strip=True)]


        
        # 提取链接
        links = []
        raw_links = soup.find_all('a', href=True)
        print(f"  [DEBUG] 页面含有 {len(raw_links)} 个原始链接标签")
        
        valid_links_count = 0
        for a in raw_links:
            href = normalize_url(a['href'], url)
            
            # 简单的调试采样 (只打印前5个和最后5个)
            if valid_links_count < 3:
                # print(f"    - 发现链接: {a['href']} -> {href}")
                pass
                
            if href:
                links.append(href)
                valid_links_count += 1
        
        print(f"  [DEBUG] 标准化后有效链接: {len(links)} 个")
        
        return {
            'title': title or urlparse(url).path.split('/')[-1] or 'Untitled',
            'url': url,
            'text': '\n\n'.join(text_parts),
            'links': links,
            'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    async def _fetch_page(
        self, 
        context: BrowserContext, 
        sem: asyncio.Semaphore, 
        url: str
    ) -> Optional[str]:
        """
        使用 Playwright 获取页面内容
        
        Args:
            context: 浏览器上下文
            sem: 并发信号量
            url: 页面URL
        
        Returns:
            渲染后的HTML内容，失败返回None
        """
        page: Optional[Page] = None
        async with sem:  # 限制并发打开的 Page 数量
            try:
                page = await context.new_page()
                
                # 设置超时
                page.set_default_timeout(self.config['timeout'] * 1000)
                
                # 访问页面
                # wait_until 可选: 'load', 'domcontentloaded', 'networkidle'
                await page.goto(url, wait_until=self.config.get('wait_until', 'domcontentloaded'))
                
                # 额外的智能等待 (用于等待 JS 渲染)
                js_wait = self.config.get('js_render_wait', 1.0)
                if js_wait > 0:
                    await asyncio.sleep(js_wait)
                
                # --- 智能滚动 (终极版 v4: 稳健的全量渲染) ---
                print(f"  [INFO] 尝试全量渲染策略...")
                
                try:
                    # 0. 预热 (飞书可能需要一点时间来撑开容器)
                    # 先给个较大的初始值，诱导它渲染
                    await page.set_viewport_size({"width": 1280, "height": 3000})
                    await asyncio.sleep(2)
                    
                    # 1. 循环检测真实高度 (防止刚进去时是骨架屏，高度很小)
                    full_height = 0
                    for _ in range(5):
                        h = await page.evaluate("document.body.scrollHeight")
                        if h > 2000: # 认为是一个合理的展开高度
                            full_height = h
                            break
                        await asyncio.sleep(1)
                    
                    # 如果还是没拿到，就用最后一次的值，或者保底 5000
                    full_height = full_height or await page.evaluate("document.body.scrollHeight")
                    
                    if full_height > 0:
                         print(f"  [DEBUG] 页面真实高度: {full_height}px, 执行视口扩张...")
                         target_height = min(full_height + 2000, 30000) # 多加2000冗余
                         await page.set_viewport_size({"width": 1280, "height": target_height})
                         await asyncio.sleep(3) # 视口变大后，React 需要时间重绘
                except Exception as e:
                    print(f"  [WARN] 视口调整失败: {e}")

                # 2. 依然执行滚动，确保触发那些基于 scroll 事件的懒加载
                # (即使视口变大了，有些图片还是需要滚动事件才能加载)
                print(f"  [INFO] 开始模拟鼠标滚轮滚动 (确保懒加载触发)...")
                
                # 将鼠标移动到页面中心
                try:
                   viewport = page.viewport_size
                   if viewport:
                       await page.mouse.move(viewport['width'] * 0.6, min(viewport['height'] * 0.5, 800))
                except: pass
                
                # 快速滚动一遍 (因为视口已经很大了，可能不需要滚太多次，但为了保险还是滚一遍)
                last_height = 0
                no_change_count = 0
                
                for i in range(50):
                    await page.mouse.wheel(0, 1000)
                    await asyncio.sleep(0.5) 
                    
                    # 检查高度
                    new_height = await page.evaluate("document.body.scrollHeight")
                    
                    # 如果当前高度已经小于视口高度，且不再变化，说明真的到底了且全显示了
                    current_scroll = await page.evaluate("window.scrollY")
                    vp_height = page.viewport_size['height']
                    
                    if new_height == last_height:
                        no_change_count += 1
                        if no_change_count >= 5:
                            break
                    else:
                        no_change_count = 0
                        last_height = new_height
                        # 如果发现高度变大了，再次扩张视口 (如果还没到上限)
                        if new_height > vp_height and new_height < 30000:
                             try:
                                await page.set_viewport_size({"width": 1280, "height": new_height + 500})
                             except: pass
                        
                    if i % 10 == 0:
                        print(f"  [DEBUG] 滚动中... ({i}/50)")

                print("  [INFO] 全量渲染处理完成")
                
                # 3. 再次等待 JS 渲染
                js_wait = self.config.get('js_render_wait', 1.0)
                if js_wait > 0:
                    await asyncio.sleep(js_wait)
                
                content = await page.content()
                return content
                
            except Exception as e:
                print(f"  ⚠️  抓取失败: {url} - {str(e)}")
                return None
            finally:
                if page:
                    await page.close()
    
    async def _crawl_recursive(
        self, 
        context: BrowserContext,
        sem: asyncio.Semaphore,
        url: str, 
        depth: int,
        start_domain: str
    ):
        """
        递归抓取页面
        """
        # 检查限制条件 (深度、总数、已访问)
        if depth > self.config['max_depth']:
            return
        if len(self.visited_urls) >= self.config['max_pages']:
            return
        
        url = normalize_url(url)
        if not url or url in self.visited_urls:
            return
        
        # 检查域名和排除规则
        if self.config['same_domain_only'] and not is_same_domain(url, f"https://{start_domain}"):
            return
        if should_exclude_url(url, self.config['exclude_patterns']):
            return
        
        # 标记已访问
        self.visited_urls.add(url)
        
        # 打印进度
        print_progress(len(self.visited_urls), self.config['max_pages'], url, depth)
        
        # 获取内容
        html = await self._fetch_page(context, sem, url)
        if not html:
            return
        
        # 提取数据
        content = self._extract_text(html, url)
        if content['text']:
            self.results.append(content)
        
        # 延迟 (Playwright模式下通常也不需要太长时间，因为本身就很慢)
        delay = self.config.get('delay', 1.0)
        if delay > 0:
            await asyncio.sleep(delay)
        
        # 递归抓取子链接
        # 注意：这里我们收集所有链接后并行处理，但受 Semaphore 限制
        tasks = []
        for link in content['links']:
            # 简单的剪枝：如果深度已满，就不再创建任务
            if depth + 1 <= self.config['max_depth']:
                if link not in self.visited_urls:
                     task = asyncio.create_task(
                         self._crawl_recursive(context, sem, link, depth + 1, start_domain)
                     )
                     tasks.append(task)
        
        # 等待所有子任务 (注意：这会变成深度优先的变体，实际执行顺序取决于调度)
        # 为了避免无限并发任务导致栈溢出或内存过大，
        # 在递归深度较大时，这种写法可能需要优化为队列模式。
        # 但考虑到 max_depth 通常很小 (2-3)，直接递归 + await 是可以接受的。
        if tasks:
            await asyncio.gather(*tasks)
    
    async def crawl(self, start_url: str) -> List[Dict[str, Any]]:
        """
        开始抓取
        """
        print(f"\n[INFO] 开始抓取 (Playwright模式): {start_url}")
        print(f"   配置: 最大深度={self.config['max_depth']}, 最大页面数={self.config['max_pages']}")
        print(f"   无头模式: {self.config.get('headless', True)}")
        print("=" * 60)
        print("=" * 60)
        
        start_domain = get_domain(start_url)
        
        # 启动 Playwright
        async with async_playwright() as p:
            # 启动浏览器
            browser = await p.chromium.launch(
                headless=self.config.get('headless', True),
                args=['--no-sandbox', '--disable-setuid-sandbox'] # Linux/Docker 环境常用，Windows这里加上也没事
            )
            
            # 创建上下文 (可以在这里注入 Cookie 或设置 UserAgent)
            context = await browser.new_context(
                user_agent=self.ua.random,
                viewport={'width': 1920, 'height': 1080},
                locale='zh-CN'
            )
            
            # 信号量限制并发页签数
            sem = asyncio.Semaphore(self.config.get('concurrency', 5))
            
            try:
                # 开始递归
                await self._crawl_recursive(context, sem, start_url, 0, start_domain)
            finally:
                await context.close()
                await browser.close()
        
        print("=" * 60)
        print(f"[SUCCESS] 抓取完成! 共抓取 {len(self.results)} 个页面\n")
        
        return self.results
    
    def save_results(self, output_dir: str = None):
        """保存抓取结果 (代码保持不变)"""
        # 复用之前的逻辑，只是为了完整性包含在这里
        # 实际代码复用时可以不写，但为了 overwrite 方便我还是写上，或者 import
        # 这里为了保持 crawler.py 独立性，保留 save 代码
        from utils import create_output_dir
        
        if not self.results:
            print("[WARN]  没有可保存的内容")
            return
        
        output_dir = output_dir or create_output_dir(self.config['output_dir'])
        output_format = self.config['output_format']
        
        print(f"[INFO] 保存到: {output_dir}")
        
        # 保存内容文件
        for i, content in enumerate(self.results, 1):
            filename = f"{i:03d}_{sanitize_filename(content['title'])}"
            filepath = f"{output_dir}/{filename}"
            save_content(content, filepath, output_format)
        
        # 保存索引
        index_path = f"{output_dir}/index.md"
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write("# 抓取结果索引\n\n")
            f.write(f"**抓取时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**起始URL:** {self.results[0]['url'] if self.results else 'N/A'}\n")
            f.write(f"**页面数量:** {len(self.results)}\n\n")
            f.write("---\n\n")
            f.write("## 页面列表\n\n")
            for i, content in enumerate(self.results, 1):
                ext = {'markdown': '.md', 'json': '.json', 'txt': '.txt'}[output_format]
                filename = f"{i:03d}_{sanitize_filename(content['title'])}{ext}"
                f.write(f"{i}. [{content['title']}](./{filename})\n")
                f.write(f"   - URL: {content['url']}\n\n")
        
        print(f"[SUCCESS] 保存完成! 共 {len(self.results)} 个文件")
