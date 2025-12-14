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
    el: document.getElementById('preview-panel'),
    cards: document.getElementById('preview-cards')
};

let currentTaskId = null;
let pollInterval = null;

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

async function startTask(endpoint) {
    const payload = {
        url: inputs.url.value,
        max_depth: parseInt(inputs.depth.value),
        max_pages: parseInt(inputs.pages.value)
    };
    
    setWorking(true);
    monitor.el.classList.remove('hidden');
    preview.el.classList.add('hidden'); // Hide previous preview
    
    monitor.badge.className = 'badge running';
    monitor.badge.innerText = 'RUNNING';
    monitor.bar.style.width = '0%';
    monitor.text.innerText = '0/0';
    monitor.url.innerText = 'Initializing...';
    
    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        currentTaskId = data.task_id;
        pollInterval = setInterval(pollStatus, 1000);
    } catch (e) {
        alert('Error starting task');
        setWorking(false);
    }
}

async function pollStatus() {
    if (!currentTaskId) return;
    
    try {
        const res = await fetch(`/api/status/${currentTaskId}`);
        if (!res.ok) return; // Wait
        const task = await res.json();
        
        // Update Progress
        const prog = task.progress;
        if (prog) {
            const pct = prog.total > 0 ? Math.min(100, Math.round((prog.current / prog.total) * 100)) : 0;
            monitor.bar.style.width = `${pct}%`;
            monitor.text.innerText = `${prog.current}/${prog.total}`;
            if (prog.url) monitor.url.innerText = `Fetching: ${prog.url}`;
        }
        
        if (task.status === 'completed' || task.status === 'failed') {
            clearInterval(pollInterval);
            setWorking(false);
            
            monitor.badge.innerText = task.status.toUpperCase();
            monitor.badge.className = `badge ${task.status}`;
            monitor.bar.style.width = '100%';
            monitor.url.innerText = task.status === 'completed' ? 'Done.' : 'Failed.';
            
            if (task.status === 'completed') {
                if (task.preview_data) {
                    renderPreview(task.preview_data);
                } else if (task.result_dir) {
                    // Start mode completed
                    btns.download.disabled = false;
                    preview.el.classList.add('hidden');
                }
            } else {
                alert('Task Failed: ' + task.error);
            }
        }
    } catch (e) {
        console.error(e);
    }
}

function renderPreview(data) {
    preview.el.classList.remove('hidden');
    preview.cards.innerHTML = data.map(item => `
        <div class="card">
            <h4>${item.title || 'Untitled'}</h4>
            <a href="${item.url}" target="_blank">${item.url}</a>
            <p>${item.text_preview ? item.text_preview.replace(/</g, '&lt;') : 'No text content...'}</p>
        </div>
    `).join('');
}

btns.preview.onclick = () => startTask('/api/preview');
btns.start.onclick = () => startTask('/api/crawl');
btns.download.onclick = () => {
    if (currentTaskId) {
        window.open(`/api/download/${currentTaskId}`, '_blank');
    }
};
