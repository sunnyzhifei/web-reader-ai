# -*- coding: utf-8 -*-
"""
网页内容递归阅读器 - 核心爬虫模块 (Playwright 版)
"""

import sys
import io

# 强制设置标准输出为 UTF-8，解决 Windows 控制台无法输出 emoji 的问题
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass  # 如果失败则忽略，不影响主流程

import asyncio
import re
import os
from bs4 import BeautifulSoup, Comment
from typing import Optional, Set, List, Dict, Any
from urllib.parse import urlparse
from datetime import datetime
from fake_useragent import UserAgent
from asyncio import Semaphore
from playwright.async_api import async_playwright, Page, BrowserContext
import inspect

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
        self.link_tree: Dict[str, List[str]] = {} # 记录页面链接结构，用于保序
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
        # DEBUG: Save the raw HTML to inspect why links are missing
        debug_path = os.path.join(self.config['output_dir'], 'debug_page.html')
        # 确保目录存在
        os.makedirs(os.path.dirname(debug_path), exist_ok=True)
        try:
            with open(debug_path, 'w', encoding='utf-8') as f:
               f.write(soup.prettify())
        except Exception as e:
            print(f"  [WARN] 无法保存调试HTML: {e}")
        settings = self.config['extract_settings']

        # --- 核心修复: 提前提取链接 ---
        # (防止 sidebar/nav 被 decompose 后连接丢失)
        links = []
        raw_links = soup.find_all('a', href=True)
        print(f"  [DEBUG] 页面含有 {len(raw_links)} 个原始链接标签 (Before Cleanup)")
        
        valid_links_count = 0
        for a in raw_links:
            href = normalize_url(a['href'], url)
            if href:
                links.append(href)
                valid_links_count += 1
        print(f"  [DEBUG] 标准化后有效链接: {len(links)} 个")
        # ---------------------------
        
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
        
        # 2. 辅助函数：处理行内元素，保留格式 (前置定义，供表格使用)
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

        # 1. 表格处理 (增强版：支持标准 Table 和 ARIA Grid/Table)
        
        # --- 飞书列表视图探测 (临时调试) ---
        owner_span = main_content.find(string=re.compile("所有者"))
        if owner_span:
            print(f"  [DEBUG] 找到 '所有者' 文本节点")
            parent = owner_span.parent
            for i in range(5):
                if parent:
                    print(f"    Parent-{i}: {parent.name} class={parent.get('class')} role={parent.get('role')}")
                    parent = parent.parent
        # --------------------------------
        
        # 预先提取所有表格，用占位符替换
        table_markdown_map = {}  # 占位符 ID -> Markdown 表格内容
        table_index = 0
        
        # 查找所有可能的表格容器
        # 1. 标准 table 标签
        # 2. ARIA 表格 (div role="table" / "grid" / "treegrid")
        potential_tables = []
        potential_tables.extend(main_content.find_all('table'))
        potential_tables.extend(main_content.select('[role="table"], [role="grid"], [role="treegrid"]'))
        
        # 去重 (防止 table 标签同时有 role 属性被添加两次)
        unique_tables = []
        seen_tables = set()
        for t in potential_tables:
            if t in seen_tables: continue
            seen_tables.add(t)
            unique_tables.append(t)

        for table in unique_tables:
            rows_data = []
            
            # --- 策略 A: 标准 HTML 表格 ---
            if table.name == 'table':
                for tr in table.find_all('tr'):
                    cells = []
                    for td in tr.find_all(['td', 'th']):
                        cell_content = process_node(td).strip()
                        cell_content = re.sub(r'\s+', ' ', cell_content).replace('\n', ' ').replace('|', '\\|')
                        cells.append(cell_content)
                    if cells:
                        rows_data.append(cells)
            
            # --- 策略 B: ARIA 伪表格 (div 结构) ---
            else:
                # 查找行 (role="row")
                raw_rows = table.select('[role="row"]')
                
                # 飞书/通用 Grid 适配：如果找不到 role="row"，尝试 class 匹配
                if not raw_rows:
                    # 尝试查找包含 row 关键字的 div
                    # 针对飞书: table-view-header-row, table-view-row
                    raw_rows = table.select('div[class*="table-view-header-row"], div[class*="table-view-row"]')
                
                for row in raw_rows:
                    cells = []
                    # 查找单元格
                    # 1. 标准 ARIA role
                    raw_cells = row.select('[role="cell"], [role="gridcell"], [role="columnheader"], [role="rowheader"]')
                    
                    # 2. 飞书适配: table-view-cell, table-view-header-cell
                    if not raw_cells:
                        raw_cells = row.select('div[class*="table-view-cell"], div[class*="table-view-header-cell"]')
                        
                    for cell in raw_cells:
                        cell_content = process_node(cell).strip()
                        cell_content = re.sub(r'\s+', ' ', cell_content).replace('\n', ' ').replace('|', '\\|')
                        cells.append(cell_content)
                    
                    if cells:
                        rows_data.append(cells)
        # --- 补充策略：精确匹配飞书/Notion等基于div的表格 ---
        # 逻辑：找到 Header -> 查找紧邻的 Body/Rows -> 独立处理每个表格
        
        # 查找所有表头容器
        # 针对飞书: class="table-view-header" (容器) 或 class="table-view-header-row" (行)
        headers = list(main_content.select('div[class*="table-view-header-row"]'))
        
        # 为了防止父子包含关系（如果 table-view-header 包含 row），我们先去重
        # 但飞书通常是平级的。
        
        for header_row in headers:
            # 检查这个 header 是否已经被处理过 (作为某个 table 的一部分被替换了)
            if header_row.parent and header_row.parent.get('data-table-processed'):
                continue
                
            # 找到包含这个 header row 的最小容器（通常是 table-view-header）
            header_container = header_row.parent
            while header_container and 'table-view-header' not in str(header_container.get('class', [])):
                if header_container.name == 'body': break
                header_container = header_container.parent
            
            if not header_container: 
                header_container = header_row.parent # fallback
            
            # 1. 提取表头数据
            header_cells = []
            # 仅查找直接子元素作为 Cell，防止递归匹配导致内容重复 (列重复/数据堆叠)
            for child in header_row.find_all(recursive=False):
                # 检查是否为 Cell 样式的 div
                classes = str(child.get('class', []))
                if 'table-view-header-cell' in classes or \
                   'table-view-cell' in classes or \
                   child.get('role') in ['columnheader', 'cell', 'gridcell']:
                    
                    header_cells.append(process_node(child).strip().replace('\n', ' ').replace('|', '\\|'))
            
            # 如果没有找到直接子元素 Cell，可能这就不是一个 Row，或者结构非常特殊
            # 这种情况下尝试查找第一层级的 Cell (深度为1)
            if not header_cells:
                 for cell in header_row.select('div[class*="table-view-header-cell"], div[class*="table-view-cell"]'):
                     # 防止无限递归，只取第一层匹配
                     if 'table-view-cell' in str(cell.parent.get('class', [])): continue 
                     header_cells.append(process_node(cell).strip().replace('\n', ' ').replace('|', '\\|'))

            if not header_cells: continue
            
            # 2. 查找对应的数据行
            # 策略：查找 header_container 的下一个兄弟，看是否是 body 或者包含 rows
            data_rows = []
            
            # 尝试一：header_container 的下一个兄弟是 Body
            next_sibling = header_container.find_next_sibling()
            rows_container = None
            
            if next_sibling:
                # 可能是 Body 容器
                if 'table-view-body' in str(next_sibling.get('class', [])) or \
                   'table-body' in str(next_sibling.get('class', [])):
                    rows_container = next_sibling
                # 或者直接就是 Row (如果是一个扁平列表)
                elif 'table-view-row' in str(next_sibling.get('class', [])):
                    # 如果直接是 row，我们需要收集所有连续的 row
                    # 这比较复杂，暂且假设有一个容器
                    rows_container = next_sibling.parent 
            
            # 如果没找到明确的 Body，尝试在 header_container 的父级中查找所有 rows
            # 并且通过位置判断（必须在 header 之后）
            if not rows_container:
                rows_container = header_container.parent
                
            if rows_container:
                # 在容器中查找所有 row
                raw_rows_selection = rows_container.select('div[class*="table-view-row"]')
                
                # 去重：过滤掉嵌套的 row (只保留最顶层的 row)
                all_possible_rows = []
                for row in raw_rows_selection:
                    # 检查父级链中是否有其他 row
                    is_nested = False
                    parent = row.parent
                    while parent and parent != rows_container:
                        if 'table-view-row' in str(parent.get('class', [])):
                            is_nested = True
                            break
                        parent = parent.parent
                    
                    if not is_nested:
                        all_possible_rows.append(row)
                
                # 找到下一个表头的位置 (用于截断)
                current_header_idx = headers.index(header_row)
                next_header_row = headers[current_header_idx + 1] if current_header_idx + 1 < len(headers) else None
                
                # 过滤：只保留在 header_row *之后* 的 row
                # 并且不能属于下一个 header (防止把下一个表格的行也抓进来)
                for row in all_possible_rows:
                    # 必须在 header 后面
                    if row.sourceline and header_row.sourceline and row.sourceline <= header_row.sourceline:
                        continue
                    
                    # 关键修复：如果行在下一个表头之后，说明属于下一个表格，停止收集
                    if next_header_row and row.sourceline and next_header_row.sourceline and row.sourceline >= next_header_row.sourceline:
                        break
                    
                    # 提取单元格 (同样应用非递归策略)
                    row_cells = []
                    for child in row.find_all(recursive=False):
                        classes = str(child.get('class', []))
                        if 'table-view-cell' in classes or \
                           child.get('role') in ['cell', 'gridcell']:
                            row_cells.append(process_node(child).strip().replace('\n', ' ').replace('|', '\\|'))
                    
                    # Fallback (同 Header)
                    if not row_cells:
                        for cell in row.select('div[class*="table-view-cell"]'):
                            if 'table-view-cell' in str(cell.parent.get('class', [])): continue 
                            row_cells.append(process_node(cell).strip().replace('\n', ' ').replace('|', '\\|'))
                    
                    if row_cells:
                        data_rows.append(row_cells)

            # --- 优化：多表格切分 ---
            # 如果 rows_container 包含多个 header，我们需要截断
            # 简单做法：如果 data_rows 里混进了下一个表格的 row，通常很难区分，
            # 除非我们按 DOM 树遍历。
            # 这里做一个简单假设：每个表格都有独立的 header container 结构。
            
            # --- 优化：对齐表头和内容 ---
            valid_rows = [header_cells] + data_rows
            
            # 过滤全空列 (飞书常有 checkbox 列)
            if valid_rows:
                num_cols = len(header_cells)
                # 检查每一列是否全空
                cols_to_keep = []
                for c in range(num_cols):
                    has_content = False
                    for r in valid_rows:
                        if c < len(r) and r[c].strip():
                            has_content = True
                            break
                    if has_content:
                        cols_to_keep.append(c)
                
                # 重构行数据，只保留有效列
                if len(cols_to_keep) < num_cols:
                    new_valid_rows = []
                    for r in valid_rows:
                        new_row = [r[i] for i in cols_to_keep if i < len(r)]
                        new_valid_rows.append(new_row)
                    valid_rows = new_valid_rows

            if len(valid_rows) < 2: continue
            
            # 生成 Markdown
            max_cols = max(len(r) for r in valid_rows)
            md_lines = []
            
            # 表头
            header_line = valid_rows[0] + [''] * (max_cols - len(valid_rows[0]))
            md_lines.append('| ' + ' | '.join(header_line) + ' |')
            md_lines.append('| ' + ' | '.join(['---'] * max_cols) + ' |')
            
            # 内容
            for row in valid_rows[1:]:
                row += [''] * (max_cols - len(row))
                md_lines.append('| ' + ' | '.join(row) + ' |')

            table_md = '\n' + '\n'.join(md_lines) + '\n'
            
            placeholder_id = f"__TABLE_PLACEHOLDER_{table_index}__"
            table_markdown_map[placeholder_id] = table_md
            
            placeholder_tag = soup.new_tag('div')
            placeholder_tag['data-table-placeholder'] = placeholder_id
            placeholder_tag.string = placeholder_id
            
            # 替换对象：这里非常关键
            # 如果我们将整个 Grid 容器替换，会把后续的表格也替换掉
            # 所以只能替换已处理的部分。
            # 针对飞书，通常整个 Table View 是一个组件，替换整个组件是安全的
            # 只要我们确定这个组件只包含一个表头
            
            target_to_replace = header_container.parent if header_container.parent else header_container
            
            # 标记已处理，防止重复
            target_to_replace['data-table-processed'] = 'true'
            target_to_replace.replace_with(placeholder_tag)
            
            table_index += 1
            print(f"  [DEBUG] 提取表格 #{table_index}: {len(valid_rows)-1} 行 (已对齐优化)")

        # 3. 遍历块级元素
        block_tags = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre', 'div', 'section'}
        text_parts = []

        for element in main_content.find_all(list(block_tags)):
            # --- 优先检查是否为表格占位符 ---
            placeholder_id = element.get('data-table-placeholder')
            if placeholder_id and placeholder_id in table_markdown_map:
                # 直接插入预存的 Markdown 表格
                text_parts.append(table_markdown_map[placeholder_id])
                continue
            
            # 过滤掉包含其他块级元素的容器 (只处理最底层的块)
            has_block_children = any(child.name in block_tags for child in element.find_all(recursive=False))
            if has_block_children:
                continue
            
            # 使用 process_node 获取带格式的文本
            rich_text = process_node(element).strip()
            
            # 清洗多余空格
            rich_text = re.sub(r'\s+', ' ', rich_text)
            rich_text = rich_text.replace(' **', '**').replace('** ', '**') 
            
            if len(rich_text) < 2:
                continue

            # --- 核心优化: 识别飞书伪标题 ---
            classes = element.get('class', [])
            class_str = ' '.join(classes).lower()
            
            # 向上查找 data-block-type (针对飞书桌面端 DOM 结构)
            block_type = ''
            parent_block = element.find_parent(lambda tag: tag.has_attr('data-block-type'))
            if parent_block:
                block_type = parent_block.get('data-block-type', '')
            
            # --- 噪音过滤 ---
            blacklist = ["附件不支持打印", "文档链接直达", "评论区", "更多分类内容", "前往语雀", "扫码登录", "转到元文档"]
            if any(noise in rich_text for noise in blacklist):
                continue
            
            level = 0
            if element.name.startswith('h'):
                try: level = int(element.name[1])
                except: pass
            elif 'heading-h1' in class_str or 'ace-line-heading-1' in class_str or block_type == 'heading1': level = 1
            elif 'heading-h2' in class_str or 'ace-line-heading-2' in class_str or block_type == 'heading2': level = 2
            elif 'heading-h3' in class_str or 'ace-line-heading-3' in class_str or block_type == 'heading3': level = 3
            elif 'heading-h4' in class_str or 'ace-line-heading-4' in class_str or block_type == 'heading4': level = 4
            elif 'heading-h5' in class_str or 'ace-line-heading-5' in class_str or block_type == 'heading5': level = 5
            elif 'heading-h6' in class_str or 'ace-line-heading-6' in class_str or block_type == 'heading6': level = 6
            elif 'title' in class_str and len(rich_text) < 50: level = 2 
            
            # 组装 Markdown
            final_text = rich_text
            
            if level > 0:
                final_text = f"\n{'#' * level} {rich_text}\n"
            elif element.name == 'li' or 'list-item' in class_str or block_type == 'bullet':
                final_text = f"- {rich_text}"
            elif block_type == 'ordered':
                final_text = f"1. {rich_text}"
            elif element.name == 'blockquote' or block_type == 'quote':
                final_text = f"> {rich_text}"
            elif element.name == 'pre' or block_type == 'code':
                final_text = f"\n```\n{rich_text}\n```\n"
            
            if final_text and (not text_parts or text_parts[-1].strip() != final_text.strip()):
                 text_parts.append(final_text)
            
        # 兜底
        if len(text_parts) < 3:
             print("  ⚠️  样式还原失败，回退到暴力提取...")
             text_parts = [main_content.get_text(separator='\n\n', strip=True)]


        
        # 链接已在开头提取
        # links = ...
        
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
                    await page.set_viewport_size({"width": 1920, "height": 3000})
                    await asyncio.sleep(5)
                    
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
                         await page.set_viewport_size({"width": 1920, "height": target_height})
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
                                await page.set_viewport_size({"width": 1920, "height": new_height + 500})
                             except: pass
                        
                    if i % 10 == 0:
                        print(f"  [DEBUG] 滚动中... ({i}/50)")

                print("  [INFO] 全量渲染处理完成")
                
                # 3. 再次等待 JS 渲染
                js_wait = self.config.get('js_render_wait', 1.0)
                if js_wait > 0:
                    await asyncio.sleep(js_wait)
                
                # Debug: Check link count in browser context
                link_count = await page.evaluate("document.querySelectorAll('a').length")
                print(f"  [DEBUG] Browser sees {link_count} <a> tags")

                # Debug: Check full text
                # full_text = await page.evaluate("document.body.innerText")
                # with open("debug_text.txt", "w", encoding="utf-8") as f:
                #    f.write(full_text)

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
        
        # 进度回调
        if self.on_progress:
            if inspect.iscoroutinefunction(self.on_progress):
                await self.on_progress(len(self.visited_urls), self.config['max_pages'], url, depth)
            else:
                self.on_progress(len(self.visited_urls), self.config['max_pages'], url, depth)
        else:
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
        tasks = []
        self.link_tree[url] = [] # Initialize children list
        
        for link in content['links']:
            # 基础过滤
            if self.config['same_domain_only'] and not is_same_domain(link, f"https://{start_domain}"):
                continue
            if should_exclude_url(link, self.config['exclude_patterns']):
                continue
            
            # 记录到结构树 (只要符合域名规则，就算子节点，用于后续排序)
            if link not in self.link_tree[url]:
                self.link_tree[url].append(link)

            # 递归任务创建
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
    
    async def crawl(self, start_url: str, on_progress=None) -> List[Dict[str, Any]]:
        """
        开始抓取
        Args:
            start_url: 起始URL
            on_progress: 进度回调函数 (curr, total, url, depth)
        """
        self.start_url = normalize_url(start_url) # Record for ordered saving
        self.on_progress = on_progress
        
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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
        """
        保存抓取结果，并执行本地链接替换
        (支持保序：按 DFS 顺序生成文件名)
        """
        from utils import create_output_dir
        
        if not self.results:
            print("[WARN]  没有可保存的内容")
            return
        
        output_dir = output_dir or create_output_dir(self.config['output_dir'])
        output_format = self.config['output_format']
        ext = {'markdown': '.md', 'json': '.json', 'txt': '.txt'}[output_format]
        
    def get_ordered_results(self):
        """
        获取排序后的结果列表 (不保存)
        """
        ordered_results = []
        visited_in_sort = set()
        
        # 建立 URL -> Content 映射以便查找
        content_map = {c['url']: c for c in self.results}
        
        def dfs_collect(u):
            if u in visited_in_sort: return
            visited_in_sort.add(u)
            
            if u in content_map:
                ordered_results.append(content_map[u])
            
            # 遍历子链接
            if u in self.link_tree:
                for child in self.link_tree[u]:
                    dfs_collect(child)
        
        # 从 Start URL 开始
        if hasattr(self, 'start_url') and self.start_url:
             dfs_collect(self.start_url)
        
        # 兜底：如果有孤立页面
        for c in self.results:
            if c['url'] not in visited_in_sort:
                ordered_results.append(c)
                
        return ordered_results

    def save_results(self, output_dir: str = None):
        """
        保存抓取结果，并执行本地链接替换
        (支持保序：按 DFS 顺序生成文件名)
        """
        from utils import create_output_dir
        
        if not self.results:
            print("[WARN]  没有可保存的内容")
            return
        
        output_dir = output_dir or create_output_dir(self.config['output_dir'])
        
        # 确保目录存在 (针对手动传入路径的情况)
        import os
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            
        output_format = self.config['output_format']
        ext = {'markdown': '.md', 'json': '.json', 'txt': '.txt'}[output_format]
        
        print(f"[INFO] 保存到: {output_dir}")
        
        # --- 重建顺序 (DFS) ---
        ordered_results = self.get_ordered_results()
        
        print(f"[INFO] 已按阅读顺序重排结果: {len(self.results)} -> {len(ordered_results)}")
        # ---------------------
        
        # 辅助函数：获取 URL 的唯一 Token (最后一段)
        def get_url_key(u):
            if not u: return ""
            # 移除 query 和 hash
            u = u.split('#')[0].split('?')[0] 
            # 移除结尾斜杠
            if u.endswith('/'): u = u[:-1]
            # 获取最后一段
            return u.split('/')[-1]

        # 1. 建立 URL Token -> 本地文件名的映射
        token_map = {}
        file_list = [] 
        
        for i, content in enumerate(ordered_results, 1):
            base_name = f"{i:03d}_{sanitize_filename(content['title'])}"
            filename = f"{base_name}{ext}"
            filepath = os.path.join(output_dir, filename)
            
            # 使用 Token 作为 Key
            key = get_url_key(content['url'])
            if key:
                token_map[key] = filename
            
            # 同时也保留完整 URL 映射 (兜底)
            token_map[content['url']] = filename
            
            save_content(content, filepath[:-len(ext)], output_format)
            file_list.append((filepath, content))
            
        # 2. 离线链接替换
        if output_format == 'markdown':
            print("[INFO] 正在执行本地链接替换 (Local Link Rewriting)...")
            replaced_count = 0
            
            for filepath, content in file_list:
                with open(filepath, 'r', encoding='utf-8') as f:
                    file_text = f.read()
                
                def replace_link(match):
                    nonlocal replaced_count
                    text = match.group(1)
                    link = match.group(2)
                    
                    # 尝试匹配
                    target = None
                    
                    # 策略A: Token 匹配
                    link_key = get_url_key(link)
                    if link_key and link_key in token_map:
                        target = token_map[link_key]
                    
                    if target:
                        replaced_count += 1
                        return f"[{text}](./{target})"
                    else:
                        return match.group(0)
                
                # 执行替换
                new_text = re.sub(r'\[([^\]]+)\]\((http[^)]+)\)', replace_link, file_text)
                
                if new_text != file_text:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(new_text)

            print(f"[INFO] 链接替换完成，共修复 {replaced_count} 个处链接")

        # 3. 保存索引
        index_path = f"{output_dir}/index.md"
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write("# 抓取结果索引\n\n")
            f.write(f"**抓取时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**总页面:** {len(ordered_results)}\n\n")
            for i, content in enumerate(ordered_results, 1):
                clean_url = content['url'].split('#')[0].split('?')[0]
                key = get_url_key(clean_url)
                filename = token_map.get(key, "unknown.md")
                f.write(f"{i}. [{content['title']}](./{filename})\n   > Origin: {clean_url}\n\n")
        
        print(f"[SUCCESS] 保存完成! 共 {len(ordered_results)} 个文件")
