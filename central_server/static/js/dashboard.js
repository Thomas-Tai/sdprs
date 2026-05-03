/**
 * SDPRS Dashboard JavaScript
 * Smart Disaster Prevention Response System
 * 
 * WebSocket-based real-time dashboard updates
 * - new_alert: Insert new alert row
 * - alert_updated: Update alert status
 * - alert_resolved: Mark alert as resolved
 * - node_status: Node online/offline status
 * - pump_status: Pump state update
 *
 * NOTE: WebSocket connection and base handleWSMessage are managed by base.html.
 *       This script overrides handleWSMessage() to add dashboard-specific logic.
 */

// ===== Dashboard State =====
let dashboardNodeStates = {};

// ===== Override base.html handleWSMessage for dashboard-specific logic =====
handleWSMessage = function(msg) {
    console.log('[WS] Message:', msg.type, msg.data);
    
    // Status bar updates (from base.html)
    updateStatusBar(msg);
    
    switch (msg.type) {
        case 'new_alert':
            // Audio + tab flash + desktop notification handled by base.html's
            // updateStatusBar/notifyOnNewAlert (Sprint A item 3). We just inject
            // the table row here.
            insertNewAlertRow(msg.data);
            break;
            
        case 'alert_updated':
            updateAlertRowStatus(msg.data.alert_id, msg.data.status);
            break;
            
        case 'alert_acknowledged':
            // Sprint A item 2: another operator (or this one) took ownership.
            // Surface in the row badge; item 3's audio loop reads ackedSet.
            updateAlertRowStatus(msg.data.alert_id, 'ACKNOWLEDGED');
            break;

        case 'alert_resolved':
            updateAlertRowStatus(msg.data.alert_id, 'RESOLVED');
            break;
            
        case 'node_status':
            // Handled by status bar update
            break;
            
        case 'pump_status':
            // Handled by status bar update
            updatePumpCount();
            break;
    }
};

// ===== Dashboard-specific Status Bar Updates =====
function dashboardUpdateStatusBar(msg) {
    const pendingEl = document.getElementById('stat-pending');
    const resolvedEl = document.getElementById('stat-resolved');
    
    if (msg.type === 'alert_resolved' && pendingEl && resolvedEl) {
        const pending = parseInt(pendingEl.textContent) || 0;
        const resolved = parseInt(resolvedEl.textContent) || 0;
        pendingEl.textContent = Math.max(0, pending - 1);
        resolvedEl.textContent = resolved + 1;
    }
}

function updatePumpCount() {
    fetch('/api/nodes?type=pump')
        .then(res => res.json())
        .then(nodes => {
            const running = nodes.filter(n => n.pump_state === 'ON').length;
            const el = document.getElementById('stat-pump-active');
            if (el) el.textContent = running;
        })
        .catch(err => console.error('Failed to update pump count:', err));
}

// ===== Connection Status Indicator =====
function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('ws-status');
    if (statusEl) {
        statusEl.className = connected ? 'text-green-500' : 'text-red-500';
        statusEl.textContent = connected ? '\ud83d\udfe2' : '\ud83d\udd34';
    }
}

// ===== Alert Row Operations =====
function insertNewAlertRow(data) {
    const tbody = document.getElementById('alerts-tbody');
    if (!tbody) return;
    
    const timestamp = formatTimestamp(data.timestamp);
    
    const tr = document.createElement('tr');
    tr.id = `alert-row-${data.alert_id}`;
    tr.className = 'alert-new bg-yellow-50 animate-pulse';
    
    function _mkTd(cls) { const td = document.createElement('td'); td.className = cls; return td; }
    const _cellCls = 'px-6 py-4 whitespace-nowrap text-sm';
    // Column order MUST match dashboard.html thead:
    //   [chk] | \u6642\u9593 | \u7b49\u5f85\u6642\u9593 | \u7bc0\u9ede ID | \u72c0\u614b | \u8996\u89ba | \u97f3\u8a0a | \u64cd\u4f5c
    // PENDING_VIDEO is not yet resolvable, so we leave the checkbox cell empty.
    tr.appendChild(_mkTd('px-3 py-4'));
    const td1 = _mkTd(_cellCls + ' text-gray-900'); td1.textContent = timestamp; tr.appendChild(td1);
    const tdAge = _mkTd(_cellCls + ' alert-age');
    tdAge.dataset.timestamp = data.timestamp;
    tdAge.dataset.status = data.status || 'PENDING_VIDEO';
    tdAge.textContent = '-';
    tr.appendChild(tdAge);
    const td2 = _mkTd(_cellCls + ' font-mono text-gray-600'); td2.textContent = data.node_id; tr.appendChild(td2);
    const td3 = _mkTd('px-6 py-4 whitespace-nowrap');
    const span3 = document.createElement('span'); span3.className = 'status-badge pending-video';
    span3.textContent = '\u23f3 \u7b49\u5f85\u5f71\u7247'; td3.appendChild(span3); tr.appendChild(td3);
    const td4 = _mkTd(_cellCls + ' text-gray-900'); td4.textContent = '-'; tr.appendChild(td4);
    const td5 = _mkTd(_cellCls + ' text-gray-900'); td5.textContent = '-'; tr.appendChild(td5);
    const td6 = _mkTd(_cellCls);
    const a6 = document.createElement('a');
    a6.href = '/alerts/' + encodeURIComponent(String(data.alert_id));
    a6.className = 'text-blue-600 hover:text-blue-800 font-medium';
    a6.textContent = '\u67e5\u770b'; td6.appendChild(a6); tr.appendChild(td6);
    
    tbody.insertBefore(tr, tbody.firstChild);
    updateAgeCell(tdAge);

    setTimeout(() => {
        tr.classList.remove('bg-yellow-50', 'animate-pulse');
    }, 2000);
    
    const pendingEl = document.getElementById('stat-pending');
    if (pendingEl) {
        const current = parseInt(pendingEl.textContent) || 0;
        pendingEl.textContent = current + 1;
    }
}

function updateAlertRowStatus(alertId, newStatus) {
    const row = document.getElementById(`alert-row-${alertId}`);
    if (!row) return;
    
    // Column order: [chk] | 時間 | 等待時間 | 節點 ID | 狀態 | ...  (item 10 added the checkbox)
    const statusCell = row.querySelector('td:nth-child(5)');
    if (!statusCell) return;
    
    const _badges = {
        'PENDING_VIDEO': ['status-badge pending-video', '\u23f3 \u7b49\u5f85\u5f71\u7247'],
        'PENDING': ['status-badge pending', '\ud83d\udd34 \u5f85\u8655\u7406'],
        'ACKNOWLEDGED': ['status-badge acknowledged', '\ud83d\udd35 \u5df2\u8a8d\u9818'],
        'RESOLVED': ['status-badge resolved', '\ud83d\udfe2 \u5df2\u8655\u7406'],
    };
    const _b = _badges[newStatus];
    if (_b) {
        statusCell.textContent = '';
        const _span = document.createElement('span');
        _span.className = _b[0];
        _span.textContent = _b[1];
        statusCell.appendChild(_span);
    }

    // Sync age cell's data-status so updateAges() can grey-out resolved rows.
    const ageCell = row.querySelector('.alert-age');
    if (ageCell) {
        ageCell.dataset.status = newStatus;
        updateAgeCell(ageCell);
    }
}

// ===== Alert age column updater (Sprint A item 4) =====
// Recomputes "等待 12 分鐘" with color thresholds. Runs every 30s + on
// row insertion / status change. RESOLVED rows are greyed out.
function updateAgeCell(cell) {
    const ts = cell.dataset.timestamp;
    if (!ts) { cell.textContent = '-'; return; }
    const status = cell.dataset.status || 'PENDING';
    const ageMs = Date.now() - new Date(ts).getTime();
    if (isNaN(ageMs) || ageMs < 0) { cell.textContent = '-'; return; }

    const ageSec = Math.floor(ageMs / 1000);
    let label;
    if (ageSec < 60)         label = `${ageSec} 秒`;
    else if (ageSec < 3600)  label = `${Math.floor(ageSec / 60)} 分鐘`;
    else if (ageSec < 86400) label = `${Math.floor(ageSec / 3600)} 小時`;
    else                     label = `${Math.floor(ageSec / 86400)} 天`;

    cell.textContent = label;
    cell.classList.remove('age-warn', 'age-orange', 'age-critical', 'age-resolved');

    if (status === 'RESOLVED') {
        cell.classList.add('age-resolved');
        return;
    }
    const ageMin = ageSec / 60;
    if (ageMin >= 30)     cell.classList.add('age-critical');
    else if (ageMin >= 15) cell.classList.add('age-orange');
    else if (ageMin >= 5)  cell.classList.add('age-warn');
}

function updateAllAges() {
    document.querySelectorAll('.alert-age').forEach(updateAgeCell);
}

// ===== Filter persistence (Sprint A item 5) =====
// On page load, if the URL has no ?status= AND localStorage has a saved
// filter, redirect once to apply it. Refreshing in the middle of a typhoon
// shouldn't dump the operator back to "全部" — that's how alerts get missed.
function applyPersistedFilterOnce() {
    const url = new URL(window.location.href);
    if (url.searchParams.has('status') || url.searchParams.has('node')) return;
    const saved = localStorage.getItem('sdprs-last-filter');
    if (!saved) return;
    try {
        const f = JSON.parse(saved);
        if (f.status) url.searchParams.set('status', f.status);
        if (f.node)   url.searchParams.set('node', f.node);
        // Avoid redirect loop: only redirect if the URL actually changes.
        if (url.toString() !== window.location.href) {
            window.location.replace(url.toString());
        }
    } catch (e) { /* corrupt localStorage; ignore */ }
}

function persistCurrentFilter() {
    const url = new URL(window.location.href);
    const filter = {
        status: url.searchParams.get('status') || '',
        node:   url.searchParams.get('node')   || '',
    };
    localStorage.setItem('sdprs-last-filter', JSON.stringify(filter));
}

// ===== Infinite scroll + new-alerts banner (item 7) =====
// Active-only mode swaps pagination for infinite scroll. Resolved/all keep
// pagination because (a) a 30-day history doesn't paginate well, and
// (b) operators investigating an old incident want stable URL anchors.
//
// During an alert storm: an operator scrolled down to investigate row #20.
// A new alert arrives; if we just prepend, their position jumps. So we
// buffer new alerts when scrollY > 200 and surface a banner that scrolls
// to top + flushes the buffer on click.

let infState = null;     // populated only in active-only mode
let pendingNewAlerts = [];

function readDashboardBootstrap() {
    try {
        const el = document.getElementById('dashboard-bootstrap');
        if (!el) return null;
        return JSON.parse(el.textContent);
    } catch (e) { return null; }
}

function setupInfiniteScroll() {
    const boot = readDashboardBootstrap();
    if (!boot) return;
    // Only enable infinite scroll when the active-only filter is in effect.
    // History views (RESOLVED, single status, "all") keep traditional pagination.
    if (boot.currentStatusFilter !== boot.activeFilter) return;

    infState = {
        loaded: boot.loadedRows,
        total:  boot.totalRows,
        nodeFilter: boot.currentNodeFilter || '',
        statusFilter: boot.currentStatusFilter,
        loading: false,
        done: boot.loadedRows >= boot.totalRows,
    };

    // Hide the server-rendered pagination block — it's incompatible with
    // infinite scroll (would double-load the same rows).
    const pagination = document.getElementById('pagination-block');
    if (pagination) pagination.classList.add('hidden');

    const sentinel = document.getElementById('infinite-scroll-sentinel');
    const endNote  = document.getElementById('infinite-scroll-end');
    if (!sentinel) return;

    if (infState.done) {
        // First page already exhausted the dataset.
        if (endNote) endNote.classList.remove('hidden');
        return;
    }
    sentinel.classList.remove('hidden');

    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) loadMoreAlerts();
    }, { rootMargin: '200px' });
    observer.observe(sentinel);
}

async function loadMoreAlerts() {
    if (!infState || infState.loading || infState.done) return;
    infState.loading = true;
    try {
        const params = new URLSearchParams();
        params.set('status', infState.statusFilter);
        if (infState.nodeFilter) params.set('node', infState.nodeFilter);
        params.set('offset', String(infState.loaded));
        params.set('limit', '20');
        const resp = await fetch('/api/alerts?' + params.toString());
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const rows = await resp.json();
        rows.forEach(appendAlertRow);
        infState.loaded += rows.length;
        if (rows.length < 20 || infState.loaded >= infState.total) {
            infState.done = true;
            const sentinel = document.getElementById('infinite-scroll-sentinel');
            const endNote  = document.getElementById('infinite-scroll-end');
            if (sentinel) sentinel.classList.add('hidden');
            if (endNote)  endNote.classList.remove('hidden');
        }
    } catch (e) {
        console.error('[Dashboard] infinite-scroll load failed:', e);
    } finally {
        if (infState) infState.loading = false;
    }
}

function appendAlertRow(event) {
    const tbody = document.getElementById('alerts-tbody');
    if (!tbody) return;
    if (document.getElementById('alert-row-' + event.id)) return;  // dedupe

    const _cellCls = 'px-6 py-4 whitespace-nowrap text-sm';
    const _badges = {
        'PENDING_VIDEO': ['status-badge pending-video', '⏳ 等待影片'],
        'PENDING':       ['status-badge pending',       '🔴 待處理'],
        'ACKNOWLEDGED':  ['status-badge acknowledged',  '🔵 已認領'],
        'RESOLVED':      ['status-badge resolved',      '🟢 已處理'],
    };

    const tr = document.createElement('tr');
    tr.id = 'alert-row-' + event.id;
    tr.className = 'alert-row hover:bg-gray-50';
    tr.dataset.alertId = String(event.id);
    tr.dataset.alertStatus = event.status;

    function _td(cls, text) { const td = document.createElement('td'); td.className = cls; if (text != null) td.textContent = text; return td; }

    // Item 10: bulk-select checkbox (only for resolvable rows).
    const tdSel = _td('px-3 py-4');
    if (event.status === 'PENDING' || event.status === 'ACKNOWLEDGED') {
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'bulk-select h-4 w-4';
        cb.value = String(event.id);
        tdSel.appendChild(cb);
    }
    tr.appendChild(tdSel);

    tr.appendChild(_td(_cellCls + ' text-gray-900', event.timestamp));

    const tdAge = _td(_cellCls + ' alert-age', '-');
    tdAge.dataset.timestamp = event.timestamp;
    tdAge.dataset.status = event.status;
    tr.appendChild(tdAge);

    tr.appendChild(_td(_cellCls + ' font-mono text-gray-600', event.node_id));

    const tdStatus = _td('px-6 py-4 whitespace-nowrap');
    const b = _badges[event.status];
    if (b) {
        const span = document.createElement('span');
        span.className = b[0];
        span.textContent = b[1];
        tdStatus.appendChild(span);
    }
    tr.appendChild(tdStatus);

    tr.appendChild(_td(_cellCls + ' text-gray-900',
        event.visual_confidence != null ? event.visual_confidence.toFixed(2) : '-'));
    tr.appendChild(_td(_cellCls + ' text-gray-900',
        event.audio_db_peak != null ? event.audio_db_peak.toFixed(1) + ' dB' : '-'));

    const tdAction = _td(_cellCls);
    const a = document.createElement('a');
    a.href = '/alerts/' + event.id;
    a.className = 'text-blue-600 hover:text-blue-800 font-medium';
    a.textContent = '查看';
    tdAction.appendChild(a);
    tr.appendChild(tdAction);

    tbody.appendChild(tr);
    updateAgeCell(tdAge);
}

// New-alerts banner: shown when WS new_alert arrives while the operator is
// scrolled below the fold. Click → scroll to top + flush buffer.
function setupNewAlertsBanner() {
    const banner = document.getElementById('new-alerts-banner');
    const btn    = document.getElementById('new-alerts-banner-btn');
    if (!banner || !btn) return;
    btn.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
        pendingNewAlerts = [];
        banner.classList.add('hidden');
        // Rows themselves were already inserted by insertNewAlertRow; the banner
        // just helps the operator notice them.
    });
}

function bumpNewAlertsBanner(data) {
    if (window.scrollY < 200) return;  // operator can already see the top of the list
    pendingNewAlerts.push(data.alert_id);
    const banner = document.getElementById('new-alerts-banner');
    const count  = document.getElementById('new-alerts-banner-count');
    if (count)  count.textContent = String(pendingNewAlerts.length);
    if (banner) banner.classList.remove('hidden');
}

// Wrap the existing handleWSMessage to surface the banner without disturbing
// the row-insert behaviour from insertNewAlertRow.
const _origDashboardHandleWS = handleWSMessage;
handleWSMessage = function(msg) {
    _origDashboardHandleWS(msg);
    if (msg && msg.type === 'new_alert' && msg.data) {
        bumpNewAlertsBanner(msg.data);
    }
};
// Hide the banner once the user scrolls back to the top.
window.addEventListener('scroll', () => {
    if (window.scrollY < 50 && pendingNewAlerts.length > 0) {
        pendingNewAlerts = [];
        const banner = document.getElementById('new-alerts-banner');
        if (banner) banner.classList.add('hidden');
    }
}, { passive: true });

// ===== Handover note (item 16) =====
function setupHandoverBar() {
    const editBtn = document.getElementById('handover-edit-btn');
    if (!editBtn) {
        console.warn('[Dashboard] Handover edit button not found');
        return;
    }
    editBtn.addEventListener('click', async () => {
        const noteText = document.getElementById('handover-note-text');
        if (!noteText) {
            console.error('[Dashboard] Handover note text element not found');
            return;
        }
        const cur = noteText.textContent.trim();
        const seed = cur === '（尚無備註）' ? '' : cur;
        const next = window.prompt('輸入新的交班備註 (空白會清除)：', seed);
        if (next === null) return;  // cancel
        editBtn.disabled = true;
        editBtn.textContent = '儲存中...';
        try {
            const r = await fetch('/api/handover/note', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note: next }),
            });
            if (!r.ok) {
                const errBody = await r.text();
                throw new Error('HTTP ' + r.status + ': ' + errBody);
            }
            const body = await r.json();
            noteText.textContent = body.note || '（尚無備註）';
            // Show success indicator briefly
            editBtn.textContent = '已儲存 ✓';
            setTimeout(() => { editBtn.textContent = '編輯'; }, 1500);
        } catch (e) {
            console.error('[Dashboard] Failed to save handover note:', e);
            alert('儲存失敗：' + e.message + '\n請重試');
            editBtn.textContent = '編輯';
        } finally {
            editBtn.disabled = false;
        }
    });
    console.log('[Dashboard] Handover bar setup complete');
}

// ===== Bulk resolve (item 10) =====
function getSelectedAlertIds() {
    return Array.from(document.querySelectorAll('.bulk-select:checked'))
        .map(cb => parseInt(cb.value, 10))
        .filter(n => !isNaN(n));
}

function refreshBulkToolbar() {
    const ids = getSelectedAlertIds();
    const bar = document.getElementById('bulk-toolbar');
    const cnt = document.getElementById('bulk-count');
    if (!bar) return;
    if (ids.length === 0) {
        bar.classList.add('hidden');
    } else {
        bar.classList.remove('hidden');
        if (cnt) cnt.textContent = String(ids.length);
    }
}

function setupBulkResolve() {
    document.addEventListener('change', (e) => {
        if (e.target.classList && e.target.classList.contains('bulk-select')) {
            refreshBulkToolbar();
        }
    });
    const all = document.getElementById('bulk-select-all');
    if (all) {
        all.addEventListener('change', () => {
            document.querySelectorAll('.bulk-select').forEach(cb => { cb.checked = all.checked; });
            refreshBulkToolbar();
        });
    }
    const clearBtn = document.getElementById('bulk-clear-btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            document.querySelectorAll('.bulk-select').forEach(cb => { cb.checked = false; });
            if (all) all.checked = false;
            refreshBulkToolbar();
        });
    }
    const goBtn = document.getElementById('bulk-resolve-btn');
    if (goBtn) {
        goBtn.addEventListener('click', async () => {
            const ids = getSelectedAlertIds();
            if (ids.length === 0) return;
            if (!confirm(`確定批次標記 ${ids.length} 筆為已處理？`)) return;
            const notes = (document.getElementById('bulk-notes') || {}).value || '';
            goBtn.disabled = true;
            try {
                const r = await fetch('/api/alerts/bulk-resolve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ids, notes }),
                });
                const data = await r.json();
                if (data.failures && data.failures.length > 0) {
                    alert(`成功 ${data.succeeded.length} 筆；失敗 ${data.failures.length} 筆。\n` +
                          data.failures.map(f => `#${f.id}: ${f.reason}`).join('\n'));
                }
                // alert_resolved WS messages will update individual rows; just refresh state.
                document.querySelectorAll('.bulk-select').forEach(cb => { cb.checked = false; });
                if (all) all.checked = false;
                refreshBulkToolbar();
            } catch (e) {
                console.error('[Dashboard] Bulk resolve failed:', e);
                alert('批次操作失敗，請重試');
            } finally {
                goBtn.disabled = false;
            }
        });
    }
}

// ===== Initialization =====
document.addEventListener('DOMContentLoaded', function() {
    // WebSocket is already connected by base.html
    // handleWSMessage has been overridden above

    applyPersistedFilterOnce();
    persistCurrentFilter();

    setupInfiniteScroll();
    setupNewAlertsBanner();
    setupHandoverBar();
    setupBulkResolve();

    // Compute ages for server-rendered rows + refresh every 30s.
    // Don't be tempted to lower the interval — minute-resolution labels mean
    // 30s is enough to catch threshold crossings without churning the DOM.
    updateAllAges();
    setInterval(updateAllAges, 30000);

    console.log('[Dashboard] Initialized');
});
