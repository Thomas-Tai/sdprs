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
    const td1 = _mkTd(_cellCls + ' text-gray-900'); td1.textContent = timestamp; tr.appendChild(td1);
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
    
    const statusCell = row.querySelector('td:nth-child(3)');
    if (!statusCell) return;
    
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

// ===== Initialization =====
document.addEventListener('DOMContentLoaded', function() {
    // WebSocket is already connected by base.html
    // handleWSMessage has been overridden above
    
    console.log('[Dashboard] Initialized');
});
