// ========== DOM 元素 ==========
const inputs = {
    url: document.getElementById('url'),
    depth: document.getElementById('max-depth'),
    pages: document.getElementById('max-pages')
};

const btns = {
    preview: document.getElementById('btn-preview'),
    start: document.getElementById('btn-start'),
    download: document.getElementById('btn-download')
};

const monitor = {
    el: document.getElementById('monitor'),
    badge: document.getElementById('status-badge'),
    text: document.getElementById('progress-text'),
    bar: document.getElementById('progress-bar'),
    url: document.getElementById('current-url')
};

const preview = {
    emptyState: document.getElementById('empty-state'),
    contentArea: document.getElementById('content-area'),
    tocList: document.getElementById('toc-list'),
    mdPreview: document.getElementById('md-preview')
};

// ========== 状态 ==========
let currentTaskId = null;
let pollInterval = null;
let previewData = []; // 存储预览数据
let currentIndex = 0; // 当前选中的文档索引

// ========== 辅助函数 ==========
function setWorking(active) {
    btns.preview.disabled = active;
    btns.start.disabled = active;
    inputs.url.disabled = active;
    inputs.depth.disabled = active;
    inputs.pages.disabled = active;
    if (active) {
        btns.download.disabled = true;
    }
}

function showEmptyState() {
    preview.emptyState.classList.remove('hidden');
    preview.contentArea.classList.add('hidden');
}

function showContentArea() {
    preview.emptyState.classList.add('hidden');
    preview.contentArea.classList.remove('hidden');
}

// ========== 目录树渲染 ==========
function renderToc(data) {
    preview.tocList.innerHTML = data.map((item, idx) => `
        <li data-index="${idx}" class="${idx === currentIndex ? 'active' : ''}" title="${item.title}">
            <span class="index-num">${String(idx + 1).padStart(2, '0')}</span>
            ${item.title || 'Untitled'}
        </li>
    `).join('');

    // 绑定点击事件
    preview.tocList.querySelectorAll('li').forEach(li => {
        li.onclick = () => {
            const idx = parseInt(li.dataset.index);
            selectDocument(idx);
        };
    });
}

// ========== 通过链接查找文档 ==========
function findDocumentByLink(href) {
    if (!href || previewData.length === 0) return -1;

    // 提取 URL 中的唯一标识符 (通常是路径的最后部分)
    // 例如: https://xxx.feishu.cn/wiki/ABC123 -> ABC123
    // 或者: ./003_Title.md -> 提取 Title 部分

    let searchToken = '';

    // 尝试从 URL 中提取 token
    const urlMatch = href.match(/\/wiki\/([A-Za-z0-9]+)/);
    if (urlMatch) {
        searchToken = urlMatch[1];
    }

    // 如果是 .md 文件格式
    if (href.endsWith('.md')) {
        const filename = href.replace('./', '').replace(/^\d+_/, '').replace('.md', '');
        // 提取可能的 token
        const tokenMatch = filename.match(/[A-Za-z0-9]{15,}/);
        if (tokenMatch) {
            searchToken = tokenMatch[0];
        }
    }

    // 在 previewData 中查找匹配的文档
    for (let i = 0; i < previewData.length; i++) {
        const item = previewData[i];
        if (!item.url) continue;

        // 策略1: Token 完全匹配
        if (searchToken && item.url.includes(searchToken)) {
            return i;
        }

        // 策略2: URL 路径匹配 (去除子域名差异)
        try {
            const linkUrl = new URL(href);
            const itemUrl = new URL(item.url);
            if (linkUrl.pathname === itemUrl.pathname) {
                return i;
            }
        } catch (e) {
            // 非标准 URL，跳过
        }
    }

    return -1;
}

// ========== 选中文档 ==========
function selectDocument(idx) {
    if (idx < 0 || idx >= previewData.length) return;

    currentIndex = idx;
    const item = previewData[idx];

    // 更新目录高亮
    preview.tocList.querySelectorAll('li').forEach((li, i) => {
        li.classList.toggle('active', i === idx);
    });

    // 渲染 Markdown
    renderMarkdown(item);
}

// ========== Markdown 渲染 ==========
function renderMarkdown(item) {
    // 构建 Markdown 内容
    let mdContent = `# ${item.title || 'Untitled'}\n\n`;

    // 添加来源链接
    if (item.url) {
        mdContent += `> 📎 来源: [${item.url}](${item.url})\n\n---\n\n`;
    }

    // 添加正文内容
    if (item.text) {
        mdContent += item.text;
    } else if (item.text_preview) {
        mdContent += item.text_preview;
    } else {
        mdContent += '*（无内容）*';
    }

    // 使用 marked.js 渲染
    try {
        preview.mdPreview.innerHTML = marked.parse(mdContent);
    } catch (e) {
        preview.mdPreview.innerHTML = `<pre>${mdContent.replace(/</g, '&lt;')}</pre>`;
    }

    // ========== 链接跳转拦截 ==========
    // 让已抓取文档的链接可以在预览区内跳转，外部链接在新标签打开
    preview.mdPreview.querySelectorAll('a').forEach(link => {
        const href = link.getAttribute('href');
        if (!href) return;

        // 尝试在 previewData 中查找匹配的文档
        let foundIdx = findDocumentByLink(href);

        if (foundIdx !== -1) {
            // 内部文档：拦截点击，在预览区跳转
            link.style.cursor = 'pointer';
            link.style.color = '#a78bfa'; // 紫色标识内部链接
            link.title = '📄 点击在预览区查看';

            link.onclick = (e) => {
                e.preventDefault();
                selectDocument(foundIdx);
            };
        } else {
            // 外部链接：在新标签页打开
            link.setAttribute('target', '_blank');
            link.setAttribute('rel', 'noopener noreferrer');
        }
    });

    // 滚动到顶部
    preview.mdPreview.scrollTop = 0;
}

// ========== UI 交互函数 ==========
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    let icon = 'ℹ️';
    if (type === 'success') icon = '✅';
    if (type === 'error') icon = '❌';

    toast.innerHTML = `
        <span class="toast-icon">${icon}</span>
        <span class="toast-msg">${message}</span>
    `;

    container.appendChild(toast);

    // 自动移除
    setTimeout(() => {
        toast.style.animation = 'toastOut 0.3s forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function shakeInput(input, labelSelector) {
    input.classList.add('invalid');
    input.focus();

    // 移除 invalid 类
    setTimeout(() => input.classList.remove('invalid'), 2000);
}

// ========== 启动任务 ==========
async function startTask(endpoint) {
    // ========== 参数验证 ==========
    const urlValue = inputs.url.value.trim();
    const depthValue = inputs.depth.value;
    const pagesValue = inputs.pages.value;

    // 检查必填项
    if (!urlValue) {
        showToast('请输入起始 URL', 'error');
        shakeInput(inputs.url);
        return;
    }

    // 验证 URL 格式
    if (!urlValue.startsWith('http://') && !urlValue.startsWith('https://')) {
        showToast('无效的 URL（必须以 http/https 开头）', 'error');
        shakeInput(inputs.url);
        return;
    }

    if (!depthValue || depthValue === '') {
        showToast('请输入最大深度', 'error');
        shakeInput(inputs.depth);
        return;
    }

    if (!pagesValue || pagesValue === '') {
        showToast('请输入最大页面数', 'error');
        shakeInput(inputs.pages);
        return;
    }

    const maxDepth = parseInt(depthValue);
    const maxPages = parseInt(pagesValue);

    // 验证数值范围
    if (isNaN(maxDepth) || maxDepth < 0 || maxDepth > 5) {
        showToast('最大深度必须是 0-5 之间的整数', 'error');
        shakeInput(inputs.depth);
        return;
    }

    if (isNaN(maxPages) || maxPages < 1 || maxPages > 1000) {
        showToast('最大页面数必须是 1-1000 之间的整数', 'error');
        shakeInput(inputs.pages);
        return;
    }

    const payload = {
        url: urlValue,
        max_depth: maxDepth,
        max_pages: maxPages
    };

    setWorking(true);
    monitor.el.classList.remove('hidden');
    showEmptyState(); // 重置预览区
    previewData = [];
    currentIndex = 0;

    monitor.badge.className = 'badge running';
    monitor.badge.innerText = 'RUNNING';
    monitor.bar.style.width = '0%';
    monitor.text.innerText = '0/0';
    monitor.url.innerText = '初始化中...';

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        // 检查响应状态
        if (!res.ok) {
            const errorData = await res.json().catch(() => ({}));
            const errorMsg = errorData.detail || `服务器错误 (${res.status})`;
            showToast(`请求失败: ${errorMsg}`, 'error');
            setWorking(false);
            monitor.el.classList.add('hidden');
            return;
        }

        const data = await res.json();
        currentTaskId = data.task_id;
        showToast('任务已启动', 'success');
        pollInterval = setInterval(pollStatus, 1000);
    } catch (e) {
        showToast('启动任务失败: 网络错误或服务未启动', 'error');
        setWorking(false);
        monitor.el.classList.add('hidden');
    }
}

// ========== 轮询状态 ==========
async function pollStatus() {
    if (!currentTaskId) return;

    try {
        const res = await fetch(`/api/status/${currentTaskId}`);
        if (!res.ok) return;
        const task = await res.json();

        // 更新进度
        const prog = task.progress;
        if (prog) {
            const pct = prog.total > 0 ? Math.min(100, Math.round((prog.current / prog.total) * 100)) : 0;
            monitor.bar.style.width = `${pct}%`;
            monitor.text.innerText = `${prog.current}/${prog.total}`;
            if (prog.url) {
                const shortUrl = prog.url.length > 50 ? prog.url.substring(0, 47) + '...' : prog.url;
                monitor.url.innerText = `抓取中: ${shortUrl}`;
            }
        }

        // 任务完成
        if (task.status === 'completed' || task.status === 'failed') {
            clearInterval(pollInterval);
            setWorking(false);

            monitor.badge.innerText = task.status.toUpperCase();
            monitor.badge.className = `badge ${task.status}`;
            monitor.bar.style.width = '100%';
            monitor.url.innerText = task.status === 'completed' ? '✅ 完成' : '❌ 失败';

            if (task.status === 'completed') {
                if (task.preview_data && task.preview_data.length > 0) {
                    // 预览模式
                    previewData = task.preview_data;
                    showContentArea();
                    renderToc(previewData);
                    selectDocument(0);
                } else if (task.result_dir) {
                    // 完整抓取模式
                    btns.download.disabled = false;
                    // 如果有 preview_data 也显示
                    if (task.preview_data && task.preview_data.length > 0) {
                        previewData = task.preview_data;
                        showContentArea();
                        renderToc(previewData);
                        selectDocument(0);
                    }
                }
            } else {
                showToast('任务失败: ' + (task.error || 'Unknown error'), 'error');
            }
        }
    } catch (e) {
        console.error('Poll error:', e);
    }
}

// ========== 绑定按钮事件 ==========
btns.preview.onclick = () => startTask('/api/preview');
btns.start.onclick = () => startTask('/api/crawl');
btns.download.onclick = () => {
    if (currentTaskId) {
        window.open(`/api/download/${currentTaskId}`, '_blank');
    }
};
