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
 */

// ===== Configuration =====
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
const RECONNECT_DELAY = 3000; // 3 seconds

// ===== State =====
let ws = null;
let isMuted = localStorage.getItem('sdprs-muted') === 'true';

// ===== WebSocket Connection =====
function connectWebSocket() {
    ws = new WebSocket(WS_URL);
    
    ws.onopen = function() {
        console.log('[WS] Connected');
        updateConnectionStatus(true);
    };
    
    ws.onmessage = function(event) {
        try {
            const msg = JSON.parse(event.data);
            handleWSMessage(msg);
        } catch (e) {
            console.error('[WS] Failed to parse message:', e);
        }
    };
    
    ws.onclose = function() {
        console.log('[WS] Disconnected');
        updateConnectionStatus(false);
        
        // Auto reconnect after delay
        setTimeout(connectWebSocket, RECONNECT_DELAY);
    };
    
    ws.onerror = function(error) {
        console.error('[WS] Error:', error);
    };
}

function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('ws-status');
    if (statusEl) {
        statusEl.className = connected ? 'text-green-500' : 'text-red-500';
        statusEl.textContent = connected ? '🟢' : '🔴';
    }
}

// ===== Message Handling =====
function handleWSMessage(msg) {
    console.log('[WS] Message:', msg.type, msg.data);
    
    // Update status bar counts
    updateStatusBar(msg);
    
    switch (msg.type) {
        case 'new_alert':
            insertNewAlertRow(msg.data);
            playAlertSound();
            break;
            
        case 'alert_updated':
            updateAlertRowStatus(msg.data.alert_id, msg.data.status);
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
}

// ===== Status Bar Updates =====
function updateStatusBar(msg) {
    // Update pending count
    const pendingEl = document.getElementById('stat-pending');
    const resolvedEl = document.getElementById('stat-resolved');
    const offlineEl = document.getElementById('stat-offline');
    const nodesEl = document.getElementById('stat-nodes');
    const pumpsEl = document.getElementById('stat-pump-active');
    
    // These would be updated based on server data
    // For now, we just increment/decrement based on message type
    if (msg.type === 'alert_resolved' && pendingEl && resolvedEl) {
        const pending = parseInt(pendingEl.textContent) || 0;
        const resolved = parseInt(resolvedEl.textContent) || 0;
        pendingEl.textContent = Math.max(0, pending - 1);
        resolvedEl.textContent = resolved + 1;
    }
}

function updatePumpCount() {
    // Fetch latest pump count from API
    fetch('/api/nodes?type=pump')
        .then(res => res.json())
        .then(nodes => {
            const running = nodes.filter(n => n.pump_state === 'ON').length;
            const el = document.getElementById('stat-pump-active');
            if (el) el.textContent = running;
        })
        .catch(err => console.error('Failed to update pump count:', err));
}

// ===== Alert Row Operations =====
function insertNewAlertRow(data) {
    const tbody = document.getElementById('alerts-tbody');
    if (!tbody) return;
    
    // Format timestamp
    const timestamp = formatTimestamp(data.timestamp);
    
    // Create new row
    const tr = document.createElement('tr');
    tr.id = `alert-row-${data.alert_id}`;
    tr.className = 'alert-new bg-yellow-50 animate-pulse';
    
    // Build cells safely to prevent XSS via node_id injection
    function _mkTd(cls) { const td = document.createElement('td'); td.className = cls; return td; }
    const _cellCls = 'px-6 py-4 whitespace-nowrap text-sm';
    // Cell 1: timestamp
    const td1 = _mkTd(_cellCls + ' text-gray-900'); td1.textContent = timestamp; tr.appendChild(td1);
    // Cell 2: node_id (user-controlled - must use textContent!)
    const td2 = _mkTd(_cellCls + ' font-mono text-gray-600'); td2.textContent = data.node_id; tr.appendChild(td2);
    // Cell 3: status badge
    const td3 = _mkTd('px-6 py-4 whitespace-nowrap');
    const span3 = document.createElement('span'); span3.className = 'status-badge pending-video';
    span3.textContent = '⏳ 等待影片'; td3.appendChild(span3); tr.appendChild(td3);
    // Cell 4-5: placeholders
    const td4 = _mkTd(_cellCls + ' text-gray-900'); td4.textContent = '-'; tr.appendChild(td4);
    const td5 = _mkTd(_cellCls + ' text-gray-900'); td5.textContent = '-'; tr.appendChild(td5);
    // Cell 6: link
    const td6 = _mkTd(_cellCls);
    const a6 = document.createElement('a');
    a6.href = '/alerts/' + encodeURIComponent(String(data.alert_id));
    a6.className = 'text-blue-600 hover:text-blue-800 font-medium';
    a6.textContent = '查看'; td6.appendChild(a6); tr.appendChild(td6);
    
    // Insert at top
    tbody.insertBefore(tr, tbody.firstChild);
    
    // Remove highlight after 2 seconds
    setTimeout(() => {
        tr.classList.remove('bg-yellow-50', 'animate-pulse');
    }, 2000);
    
    // Update pending count
    const pendingEl = document.getElementById('stat-pending');
    if (pendingEl) {
        const current = parseInt(pendingEl.textContent) || 0;
        pendingEl.textContent = current + 1;
    }
}

function updateAlertRowStatus(alertId, newStatus) {
    const row = document.getElementById(`alert-row-${alertId}`);
    if (!row) return;
    
    const statusCell = row.querySelector('td:nth-child(3)');
    if (!statusCell) return;
    
    // Build status badge safely to prevent XSS
    const _badges = {
        'PENDING_VIDEO': ['status-badge pending-video', '\u23f3 \u7b49\u5f85\u5f71\u7247'],
        'PENDING': ['status-badge pending', '\ud83d\udd34 \u5f85\u8655\u7406'],
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
}

// ===== Audio Alert =====
function playAlertSound() {
    if (isMuted) return;
    
    const audio = document.getElementById('alert-audio');
    if (audio) {
        audio.currentTime = 0;
        audio.play().catch(e => console.log('Audio play failed:', e));
    }
}

function toggleMute() {
    isMuted = !isMuted;
    localStorage.setItem('sdprs-muted', isMuted);
    
    const btn = document.getElementById('mute-btn');
    if (btn) {
        btn.textContent = isMuted ? '🔇' : '🔊';
        btn.title = isMuted ? '取消靜音' : '靜音';
    }
}

// ===== Utility Functions =====
function formatTimestamp(isoString) {
    if (!isoString) return '-';
    
    try {
        const date = new Date(isoString);
        return date.toLocaleString('zh-TW', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    } catch (e) {
        return isoString;
    }
}

// ===== Mobile Menu =====
function setupMobileMenu() {
    const btn = document.getElementById('mobile-menu-btn');
    const menu = document.getElementById('mobile-menu');
    
    if (btn && menu) {
        btn.addEventListener('click', () => {
            menu.classList.toggle('hidden');
        });
    }
}

// ===== Initialization =====
document.addEventListener('DOMContentLoaded', function() {
    // Connect WebSocket
    connectWebSocket();
    
    // Setup mute button
    const muteBtn = document.getElementById('mute-btn');
    if (muteBtn) {
        muteBtn.textContent = isMuted ? '🔇' : '🔊';
        muteBtn.addEventListener('click', toggleMute);
    }
    
    // Setup mobile menu
    setupMobileMenu();
    
    console.log('[Dashboard] Initialized');
});

// Export for global access (used by inline scripts)
window.handleWSMessage = handleWSMessage;
window.formatTimestamp = formatTimestamp;