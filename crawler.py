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
        self.visited_keys: Set[str] = set() # 用于去重 (主域名+路径)
        self.completed_count: int = 0
        self.results: List[Dict[str, Any]] = []
        self.link_tree: Dict[str, List[str]] = {} # 记录页面链接结构，用于保序
        self.ua = UserAgent()

    def _get_unique_key(self, url: str) -> str:
        """
        生成用于去重的唯一Key
        - 对于 飞书/Lark: 忽略子域名和查询参数，只看 Path (根域名+路径)
        - 对于 其他网站: 使用完整 URL (包含子域名和查询参数)
        这样既解决了飞书的重复抓取问题，又不会破坏依赖 query 参数的普通网站。
        """
        root_domain = get_domain(url, extract_root=True)
        parsed = urlparse(url)
        
        # 针对飞书/Lark 的特定优化
        if root_domain in ['feishu.cn', 'larksuite.com']:
            return f"{root_domain}{parsed.path}"
            
        # 通用策略：完整 URL (已在 normalize_url 中去除了 hash 和 trailing slash)
        return url
        
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

        # --- 策略：构建全局文本链接映射 (Text -> URL) ---
        # 飞书正文中的列表项往往没有 href，但左侧目录树里有。
        # 我们利用左侧目录树的信息来"补全"正文中的死链接。
        text_to_link_map = {}
        # 为了避免误匹配，我们设定最小文本长度，并忽略太通用的词
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            href = a['href']
            # 只有当文本长度合适且不是纯数字/符号时才记录
            if len(text) > 1 and not text.isdigit() and href:
                # 飞书特定优化：侧边栏链接通常包含 token，这是高质量链接
                full_href = normalize_url(href, url)
                if full_href:
                     text_to_link_map[text] = full_href
        


        # --- 1. 定位主要内容区域 (Main Content) ---
        # 优先在去噪之前定位，以免删除了不该删的容器
        main_content = None
        
        # 飞书等现代文档通常有明确的容器
        # 调整策略：优先抓取最外层的内容容器，防止抓取局部
        possible_selectors = [
            '.doc-content',          # 飞书标准内容容器
            '.article-content',      # 通用
            '#doc-content',
            '.main-content',
            'main',
            '[role="main"]',
            '.render-unit-wrapper',  # 降级：如果上面的都没找到，再试这个
            'article'
        ]
        
        for selector in possible_selectors:
            main_content = soup.select_one(selector)
            if main_content:

                break
        
        # 降级策略: 如果找不到特定容器，使用 body，但尝试排除 sidebar
        if not main_content:
            main_content = soup.find('body') or soup


        # --- 2. 从 Main Content 提取链接 ---
        # 仅提取正文内的链接，避免抓取侧边栏/导航栏
        links = []
        raw_links = []
        if main_content:
            # 标准链接
            raw_links.extend(main_content.find_all('a', href=True))
            # 隐式链接 (data-href / data-url) - 飞书等SPA常用
            raw_links.extend(main_content.select('[data-href], [data-url]'))
            
            # --- 关键修复: 将文本映射的链接也加入待抓取队列 ---
            # 因为我们在生成 Markdown 时会把匹配 map 的纯文本变成链接
            # 所以这里也必须把它们加入队列，否则只会生成链接却不会去爬
            for text_node in main_content.find_all(string=True):
                stripped = text_node.strip()
                if len(stripped) > 1 and stripped in text_to_link_map:
                    # 避免重复添加 (虽然 links 后续会去重，但为了效率)
                    # 注意：raw_links 是 Tag 列表，这里我们直接把 URL 加到 links 里可能更好
                    # 但为了保持逻辑统一，我们在下面的循环里处理
                    pass
        else:
            raw_links.extend(soup.find_all('a', href=True))
            raw_links.extend(soup.select('[data-href], [data-url]'))
            

        
        valid_links_count = 0
        
        # 1. 处理 Tag 链接
        for tag in raw_links:
            url_val = tag.get('href') or tag.get('data-href') or tag.get('data-url')
            if not url_val: continue
            
            href = normalize_url(url_val, url)
            if href:
                links.append(href)
                valid_links_count += 1
        
        # 2. 处理文本补全链接 (仅在 main_content 模式下)
        if main_content:
            for text_node in main_content.find_all(string=True):
                stripped = text_node.strip()
                if len(stripped) > 1 and stripped in text_to_link_map:
                    target_url = text_to_link_map[stripped]
                    if target_url not in links: # 简单去重
                        links.append(target_url)
                        valid_links_count += 1
                        


        # --- 3. 去除噪音元素 (仅在 main_content 内部操作，如果我们没复制 main_content) ---
        # 注意: 如果 main_content 只是 soup 的一部分引用，decompose 会影响 soup，也会影响 main_content
        
        # 移除不需要的标签
        if settings['remove_scripts']:
            for script in main_content.find_all('script'):
                script.decompose()
        
        if settings['remove_styles']:
            for style in main_content.find_all('style'):
                style.decompose()
        
        if settings['remove_comments']:
            for comment in main_content.find_all(string=lambda text: isinstance(text, Comment)):
                comment.extract()
        
        # 移除导航、页脚等非正文区域 (通常正文容器里不应该包含这些，但防万一)
        # 注意：不要删除 div，否则可能把内容删了
        for tag in main_content.find_all(['nav', 'footer', 'header', 'aside', 'iframe', 'noscript']):
            # 飞书有时候用 header 做标题容器，所以要小心
            # 如果是 header 且包含 h1/h2，可能有用，保留
            if tag.name == 'header' and tag.find(['h1', 'h2']):
                continue
            tag.decompose()
        
        # 提取标题
        title = ''
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)
            
        # 2. 辅助函数：处理行内元素，保留格式 (前置定义，供表格使用)
        def process_node(node):
            if isinstance(node, str):
                # 尝试对纯文本进行链接补全
                stripped = node.strip()
                
                # 防止在已经是链接的情况下重复添加 (双重链接问题)
                # 必须检查所有祖先节点，不仅仅是直接父级 (例如 <a><span>Text</span></a>)
                # 同时也要检查 data-href/data-url 的容器，因为它们也会被处理成链接
                is_link_container = lambda tag: tag.name == 'a' or tag.has_attr('data-href') or tag.has_attr('data-url')
                if node.find_parent(is_link_container):
                    return node
                
                if stripped in text_to_link_map:
                    return f"[{node}]({text_to_link_map[stripped]})"
                return node
            
            # 忽略隐藏元素
            if node.name in ['style', 'script', 'noscript', 'iframe']:
                return ''
            
            # --- 安全优化: 忽略飞书列表的显式序号 ---
            # 因为我们在 Markdown 列表输出时会自动带上序号
            if node.name == 'div' and 'order' in node.get('class', []):
                 # 确保只过滤纯序号 (如 "1.")
                 txt = node.get_text(strip=True)
                 if re.match(r'^\d+\.?$', txt):
                     return ''
            
            content = ''
            if hasattr(node, 'children'):
                for child in node.children:
                    content += process_node(child)
                
            # 处理链接
            href = None
            if node.name == 'a':
                href = node.get('href')
            elif node.has_attr('data-href'):
                href = node.get('data-href')
            elif node.has_attr('data-url'):
                href = node.get('data-url')
            
            # --- 双重链接防护 (第一道防线) ---
            # 如果子内容已经是链接格式，不要再做任何链接处理
            stripped_content = content.strip()
            contains_link = bool(re.search(r'\[.+\]\(.+\)', stripped_content))
            if contains_link:
                return stripped_content
            
            # 补全策略：如果节点本身没有链接，但其纯文本内容在映射表中
            if not href and stripped_content in text_to_link_map:
                href = text_to_link_map[stripped_content]
                
            if href:
                full_url = normalize_url(href, url)
                if full_url and stripped_content:
                    if not full_url.startswith('javascript:'):
                        return f"[{stripped_content}]({full_url})"
                    else:
                        return content
            
            # 处理加粗、斜体等
            if node.name in ['strong', 'b']:
                return f"**{content.strip()}**"
            if node.name in ['em', 'i']:
                return f"*{content.strip()}*"
            if node.name == 'code':
                return f"`{content.strip()}`"
            if node.name == 'br':
                return "\n"
                
            return content

        # 1. 表格处理 (增强版：支持标准 Table 和 ARIA Grid/Table)
        

        
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
            is_independent_body = False
            
            if next_sibling:
                # 可能是 Body 容器
                classes = str(next_sibling.get('class', []))
                if 'table-view-body' in classes or 'table-body' in classes:
                    rows_container = next_sibling
                    is_independent_body = True
                # 或者直接就是 Row (如果是一个扁平列表)
                elif 'table-view-row' in classes:
                    rows_container = next_sibling.parent 
            
            # 如果没找到明确的 Body，尝试在 header_container 的父级中查找所有 rows
            if not rows_container:
                rows_container = header_container.parent
                
            if rows_container:
                # 在容器中查找所有 row
                raw_rows_selection = rows_container.select('div[class*="table-view-row"]')
                
                # 去重：过滤掉嵌套的 row (只保留最顶层的 row)
                all_possible_rows = []
                for row in raw_rows_selection:
                    is_nested = False
                    parent = row.parent
                    while parent and parent != rows_container:
                        if 'table-view-row' in str(parent.get('class', [])):
                            is_nested = True
                            break
                        parent = parent.parent
                    if not is_nested:
                        all_possible_rows.append(row)
                
                # 找到下一个表头的位置 (用于截断 - 仅当多个表格平铺在同一个容器时需要)
                current_header_idx = headers.index(header_row)
                next_header_row = headers[current_header_idx + 1] if current_header_idx + 1 < len(headers) else None
                
                # 过滤逻辑
                for row in all_possible_rows:
                    # 1. 前置检查 (仅当 Header 和 Row 混在一个容器时需要)
                    if not is_independent_body:
                        # 如果行号存在且小于表头行号，跳过
                        if row.sourceline and header_row.sourceline and row.sourceline < header_row.sourceline:
                            continue
                        # 如果行号相同 (压缩HTML)，我们需要确保它是文档中的后继节点
                        # 简单起见，既然已经用了 select (文档顺序)，只要它不是 header 及其祖先即可
                        # 这里我们假设 select 顺序正确，如果不做 sourceline 检查，默认就是后面的
                        pass
                    
                    # 2. 截断检查 (防止吞掉下一个表格)
                    # 只有当下一个 header 也在这个 container 里时才需要截断
                    if next_header_row:
                        # 检查下一个 header 是否也在当前的 rows_container 里 (或是其后代)
                        # 如果 next_header 在这里，那我们需要在遇到它之前停止
                        if next_header_row in rows_container.descendants or next_header_row == rows_container:
                             if row.sourceline and next_header_row.sourceline and row.sourceline >= next_header_row.sourceline:
                                break
                    
                    # 提取单元格 (同样应用非递归策略)
                    row_cells = []
                    cell_idx = 0
                    for child in row.find_all(recursive=False):
                        classes = str(child.get('class', []))
                        if 'table-view-cell' in classes or \
                           child.get('role') in ['cell', 'gridcell']:
                            
                            row_cells.append(process_node(child).strip().replace('\n', ' ').replace('|', '\\|'))
                            cell_idx += 1
                    
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


        # 3. 遍历块级元素
        block_tags = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre', 'div', 'section'}
        text_parts = []
        processed_elements = set()

        for element in main_content.find_all(list(block_tags)):
            if element in processed_elements:
                continue
                
            # --- 优先检查是否为表格占位符 ---
            placeholder_id = element.get('data-table-placeholder')
            if placeholder_id and placeholder_id in table_markdown_map:
                # 直接插入预存的 Markdown 表格
                text_parts.append(table_markdown_map[placeholder_id])
                # 表格是一个整体，其内部元素不需要再遍历
                for child in element.find_all():
                    processed_elements.add(child)
                continue
            
            # 过滤掉包含其他块级元素的容器 (只处理最底层的块)
            # EXCEPTION: 代码块通常包含多行 div/p，但必须作为整体处理
            # 所以我们需要先检查是不是代码块容器
            
            # --- 核心优化: 识别飞书伪标题/代码块 ---
            classes = element.get('class', [])
            class_str = ' '.join(classes).lower()
            
            # 向上查找 data-block-type (针对飞书桌面端 DOM 结构)
            block_type = ''
            parent_block = element.find_parent(lambda tag: tag.has_attr('data-block-type'))
            if parent_block:
                block_type = parent_block.get('data-block-type', '')
            
            # --- 新增: 代码块处理 ---
            # 1. 标准 pre 标签
            # 2. 飞书 code block (data-block-type="code")
            # 3. 必须是代码块的 ROOT 容器，防止处理每一行
            is_code_block = False
            if element.name == 'pre' or \
               (block_type == 'code' and 'code-block' in class_str and 'code-block-content' not in class_str) or \
               ('code-block' in class_str and 'code-block-content' not in class_str):
                is_code_block = True
            
            # 如果仅仅是 block_type == 'code'，可能是内部的行，我们需要找到最外层的容器
            # 这里的逻辑比较微妙：find_all 会先返回父级，再返回子级吗？
            # BS4 find_all 是按文档顺序。父级先于子级。
            # 所以只要我们处理了父级并标记了子级，就不会重复处理。
            
            if is_code_block:
                code_content = element.get_text(separator='\n')
                # 尝试提取语言
                lang = ''
                # 飞书通常在父级或自身 class 里有 language-xxx
                for cls in classes:
                    if cls.startswith('language-'):
                        lang = cls.replace('language-', '')
                        break
                if not lang and parent_block:
                     for cls in parent_block.get('class', []):
                        if cls.startswith('language-'):
                            lang = cls.replace('language-', '')
                            break
                
                # 精确提取: 尝试找 code 标签或 .code-block-content
                # 这样可以去除行号等噪音
                content_container = element.select_one('.code-block-content, code')
                if content_container:
                     # 智能换行策略：
                     # 1. 优先识别飞书/AceEditor 的行结构 (.ace-line)
                     ace_lines = content_container.select('.ace-line')
                     if ace_lines:
                         lines = []
                         for line in ace_lines:
                             # get_text() 默认合并所有子节点文本，不加换行，
                             # 这正是我们想要的：保持行内高亮元素(span)紧凑连接
                             lines.append(line.get_text())
                         code_content = '\n'.join(lines)
                     else:
                         # 2. 备用：尝试直接子 div (其他编辑器结构)
                         rows = content_container.find_all('div', recursive=False)
                         if rows:
                             lines = []
                             for row in rows:
                                 lines.append(row.get_text())
                             code_content = '\n'.join(lines)
                         else:
                             # 3. Fallback: 纯文本或 pre>code
                             code_content = content_container.get_text()
                else:
                    # Fallback (没有找到 content 容器)
                    code_content = element.get_text()
                
                final_text = f"\n```{lang}\n{code_content}\n```\n"
                text_parts.append(final_text)
                
                # 标记所有后代为已处理，防止拆分
                for child in element.find_all():
                    processed_elements.add(child)
                continue 

            # 对于非代码块，保持原来的“只处理最底层块”逻辑
            has_block_children = any(child.name in block_tags for child in element.find_all(recursive=False))
            # 如果有子块（且不是代码块），说明它是容器，跳过它，等遍历到子块再说
            if has_block_children:
                continue
            
            # 使用 process_node 获取带格式的文本
            rich_text = process_node(element).strip()
            
            # 清洗多余空格
            rich_text = re.sub(r'\s+', ' ', rich_text)
            rich_text = rich_text.replace(' **', '**').replace('** ', '**') 
            
            if len(rich_text) < 2:
                continue

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
            elif block_type == 'todo' or 'todo-item' in class_str: # 待办事项
                 final_text = f"- [ ] {rich_text}"
            elif block_type == 'quote' or 'quote-block' in class_str: # 引用
                 final_text = f"> {rich_text}"
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
                        


                print("  [INFO] 全量渲染处理完成")
                
                # --- 关键修复: 滚回顶部 ---
                # 很多虚拟滚动列表在滚到底部后，会卸载顶部的 DOM 以节省内存。
                # 我们必须滚回顶部，确保开头的章节 (1.1, 1.2) 被重新渲染。
                # 由于我们前面扩大了 viewport，理论上滚回顶部后，只要高度够大，
                # 应该能同时保留顶部和中间的内容 (如果内存允许)。
                print("  [DEBUG] 正在滚回顶部以重新渲染首屏内容...")
                
                # --- 智能滚顶: 查找真实滚动容器 ---
                # 很多应用(如飞书)是 div 滚动而不是 window 滚动
                await page.evaluate("""() => {
                    window.scrollTo(0, 0);
                    
                    // 找到所有可滚动的元素
                    const scrollables = [];
                    document.querySelectorAll('*').forEach(el => {
                        if (el.scrollHeight > el.clientHeight && el.clientHeight > 0) {
                            scrollables.push(el);
                        }
                    });
                    
                    // 假设最大的那个是主滚动区
                    if (scrollables.length > 0) {
                        scrollables.sort((a, b) => b.scrollHeight - a.scrollHeight);
                        scrollables[0].scrollTo(0, 0);
                        console.log('Scrolled container:', scrollables[0].className);
                    }
                }""")
                await asyncio.sleep(2.0)
                

                
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
        # 检查是否已访问 (使用唯一Key)
        unique_key = self._get_unique_key(url)
        if unique_key in self.visited_keys:
            return
            
        # 检查域名和排除规则
        if self.config['same_domain_only'] and not is_same_domain(url, f"https://{start_domain}"):
            return
        if should_exclude_url(url, self.config['exclude_patterns']):
            return
        
        # 标记已访问
        self.visited_urls.add(url) # 记录原始URL用于展示
        self.visited_keys.add(unique_key) # 记录Key用于去重
        
        # 移除了此处的进度回调，改为在处理完成后回调，确保进度条"从0开始，完成一个涨一个"

        # 获取内容
        html = await self._fetch_page(context, sem, url)
        if not html:
            # 即使失败也算完成一个任务
            self.completed_count += 1
            if self.on_progress:
                if inspect.iscoroutinefunction(self.on_progress):
                    await self.on_progress(self.completed_count, self.config['max_pages'], url, depth)
                else:
                    self.on_progress(self.completed_count, self.config['max_pages'], url, depth)
            else:
                 print_progress(self.completed_count, self.config['max_pages'], url, depth)
            return
        
        # 提取数据
        content = self._extract_text(html, url)
        if content['text']:
            self.results.append(content)
            
        # 页面处理完成 (成功) -> 增加进度
        self.completed_count += 1
        if self.on_progress:
            if inspect.iscoroutinefunction(self.on_progress):
                await self.on_progress(self.completed_count, self.config['max_pages'], url, depth)
            else:
                self.on_progress(self.completed_count, self.config['max_pages'], url, depth)
        else:
             print_progress(self.completed_count, self.config['max_pages'], url, depth)
        
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
