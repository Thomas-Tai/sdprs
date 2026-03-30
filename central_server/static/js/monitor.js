/**
 * SDPRS Monitor Wall JavaScript
 * Smart Disaster Prevention Response System
 * 
 * Real-time camera snapshot grid with:
 * - 1fps auto-refresh
 * - Stale detection (>10s without update)
 * - Offline detection
 * - WebSocket status updates
 *
 * NOTE: WebSocket connection is managed by base.html.
 *       This script overrides handleWSMessage() to add monitor-specific logic.
 */

// ===== Configuration =====
const SNAPSHOT_REFRESH_INTERVAL = 1000;  // 1 second
const STALE_CHECK_INTERVAL = 5000;       // 5 seconds
const STALE_THRESHOLD = 10;              // 10 seconds
const RECONNECT_DELAY = 3000;

// ===== State =====
let nodeStates = {};

// ===== Snapshot Refresh =====
function refreshSnapshots() {
    const cards = document.querySelectorAll('.snapshot-card');
    const now = Date.now();
    
    cards.forEach(card => {
        const nodeId = card.dataset.nodeId;
        const img = document.getElementById(`snapshot-${nodeId}`);
        
        if (img) {
            // Add timestamp to prevent browser caching
            img.src = `/api/edge/${nodeId}/snapshot/latest?t=${now}`;
        }
    });
}

// ===== Node Status Check =====
async function checkNodeStatus() {
    try {
        const response = await fetch('/api/nodes');
        if (!response.ok) return;
        
        const nodes = await response.json();
        
        nodes.forEach(node => {
            nodeStates[node.node_id] = node;
            updateNodeCard(node);
        });
        
    } catch (err) {
        console.error('[Monitor] Failed to check node status:', err);
    }
}

function updateNodeCard(node) {
    const card = document.querySelector(`.snapshot-card[data-node-id="${node.node_id}"]`);
    if (!card) return;
    
    // Update status dot
    const statusDot = document.getElementById(`status-dot-${node.node_id}`);
    // Update overlays
    const offlineOverlay = document.getElementById(`offline-overlay-${node.node_id}`);
    const staleOverlay = document.getElementById(`stale-overlay-${node.node_id}`);
    // Update time
    const timeEl = document.getElementById(`snapshot-time-${node.node_id}`);
    
    // Determine status
    const isOffline = node.status === 'OFFLINE';
    const isStale = node.is_stale || false;
    
    // Update status dot color
    if (statusDot) {
        if (isOffline) {
            statusDot.className = 'status-dot bg-red-500';
        } else if (isStale) {
            statusDot.className = 'status-dot bg-yellow-500';
        } else {
            statusDot.className = 'status-dot bg-green-500';
        }
    }
    
    // Update overlays
    if (offlineOverlay) {
        if (isOffline) {
            offlineOverlay.classList.remove('hidden');
        } else {
            offlineOverlay.classList.add('hidden');
        }
    }
    
    if (staleOverlay) {
        if (!isOffline && isStale) {
            staleOverlay.classList.remove('hidden');
        } else {
            staleOverlay.classList.add('hidden');
        }
    }
    
    // Update snapshot timestamp
    if (timeEl && node.snapshot_timestamp) {
        timeEl.textContent = formatTimestamp(node.snapshot_timestamp);
    }
}

// ===== Override base.html handleWSMessage to add monitor-specific logic =====
handleWSMessage = function(msg) {
    // Status bar updates (from base.html logic)
    updateStatusBar(msg);
    
    // Monitor-specific updates
    switch (msg.type) {
        case 'node_status':
            if (msg.data && msg.data.node_id) {
                nodeStates[msg.data.node_id] = {
                    ...nodeStates[msg.data.node_id],
                    ...msg.data
                };
                updateNodeCard(nodeStates[msg.data.node_id]);
            }
            break;
            
        case 'pump_status':
            if (msg.data && msg.data.node_id) {
                nodeStates[msg.data.node_id] = {
                    ...nodeStates[msg.data.node_id],
                    node_id: msg.data.node_id,
                    type: 'pump',
                    status: 'ONLINE',
                    pump_state: msg.data.pump_state,
                    water_level: msg.data.water_level,
                    timestamp: msg.data.timestamp
                };
                renderPumpCard(nodeStates[msg.data.node_id]);
            }
            break;
    }
};


// ===== Pump Node Cards =====
function getPumpStateClass(state) {
    if (state === 'ON') return 'bg-red-100 text-red-800';
    if (state === 'OFF') return 'bg-green-100 text-green-800';
    return 'bg-gray-100 text-gray-600';
}

function getPumpStateLabel(state) {
    if (state === 'ON') return '運行中';
    if (state === 'OFF') return '待機';
    return state || '未知';
}

function getWaterBarColor(level) {
    if (level >= 80) return 'bg-blue-600';
    if (level >= 40) return 'bg-blue-400';
    return 'bg-blue-200';
}

function renderPumpCard(data) {
    const grid = document.getElementById('pump-nodes-grid');
    if (!grid) return;

    const nodeId = data.node_id;
    const level = typeof data.water_level === 'number' ? data.water_level.toFixed(1) : '--';
    const levelNum = typeof data.water_level === 'number' ? data.water_level : 0;
    const state = data.pump_state || 'UNKNOWN';
    const stateClass = getPumpStateClass(state);
    const stateLabel = getPumpStateLabel(state);
    const barColor = getWaterBarColor(levelNum);
    const isOnline = data.status !== 'OFFLINE';
    const dotClass = isOnline ? 'bg-green-500' : 'bg-red-500';

    // Hide empty placeholder
    const emptyEl = document.getElementById('pump-nodes-empty');
    if (emptyEl) emptyEl.classList.add('hidden');

    let card = document.getElementById('pump-card-' + nodeId);
    if (!card) {
        // Create new card
        card = document.createElement('div');
        card.className = 'bg-white rounded-lg shadow p-4';
        card.id = 'pump-card-' + nodeId;
        card.innerHTML = `
            <div class="flex justify-between items-center mb-3">
                <span class="font-mono text-sm font-bold">${nodeId}</span>
                <span id="pump-dot-${nodeId}" class="status-dot ${dotClass}"></span>
            </div>
            <div class="mb-3">
                <div class="flex justify-between text-sm text-gray-600 mb-1">
                    <span>雨量感測</span>
                    <span id="pump-water-pct-${nodeId}">${level}%</span>
                </div>
                <div class="w-full bg-gray-200 rounded-full h-3">
                    <div id="pump-water-bar-${nodeId}"
                         class="h-3 rounded-full transition-all duration-500 ${barColor}"
                         style="width: ${Math.min(100, levelNum)}%"></div>
                </div>
            </div>
            <div class="flex justify-between items-center">
                <span class="text-sm text-gray-600">水泵狀態</span>
                <span id="pump-state-badge-${nodeId}"
                      class="px-2 py-1 rounded text-sm font-medium ${stateClass}">
                    ${stateLabel}
                </span>
            </div>
            <div class="mt-2 text-xs text-gray-400 text-right">
                更新: <span id="pump-last-update-${nodeId}">--</span>
            </div>
        `;
        grid.appendChild(card);
    } else {
        // Update existing card elements
        const dotEl = document.getElementById('pump-dot-' + nodeId);
        if (dotEl) dotEl.className = 'status-dot ' + dotClass;

        const pctEl = document.getElementById('pump-water-pct-' + nodeId);
        if (pctEl) pctEl.textContent = level + '%';

        const barEl = document.getElementById('pump-water-bar-' + nodeId);
        if (barEl) {
            barEl.className = 'h-3 rounded-full transition-all duration-500 ' + barColor;
            barEl.style.width = Math.min(100, levelNum) + '%';
        }

        const badgeEl = document.getElementById('pump-state-badge-' + nodeId);
        if (badgeEl) {
            badgeEl.className = 'px-2 py-1 rounded text-sm font-medium ' + stateClass;
            badgeEl.textContent = stateLabel;
        }
    }

    // Update timestamp
    const tsEl = document.getElementById('pump-last-update-' + nodeId);
    if (tsEl) {
        const now = new Date();
        tsEl.textContent = now.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
}

async function loadPumpNodes() {
    try {
        const response = await fetch('/api/nodes');
        if (!response.ok) return;
        const nodes = await response.json();
        nodes.forEach(node => {
            if (node.type === 'pump') {
                nodeStates[node.node_id] = node;
                renderPumpCard(node);
            }
        });
    } catch (err) {
        console.error('[Monitor] Failed to load pump nodes:', err);
    }
}

// ===== Image Error Handling =====
function setupImageErrorHandlers() {
    document.querySelectorAll('.snapshot-card img').forEach(img => {
        img.onerror = function() {
            console.log(`[Monitor] Image load error for ${img.id}`);
        };
    });
}

// ===== Lightbox =====
let lightboxNodeId = null;
let lbScale = 1;
let lbPanX = 0;
let lbPanY = 0;
let lbDragging = false;
let lbDragStart = { x: 0, y: 0 };
let lbRefreshTimer = null;
let lbInitDist = 0;
let lbInitScale = 1;

const LB_MIN_SCALE = 0.5;
const LB_MAX_SCALE = 8;

function openLightbox(nodeId) {
    lightboxNodeId = nodeId;
    lbScale = 1;
    lbPanX = 0;
    lbPanY = 0;

    const lb = document.getElementById('lightbox');
    const img = document.getElementById('lightbox-img');
    document.getElementById('lightbox-title').textContent = nodeId;
    document.getElementById('lightbox-link').href = '/?node=' + nodeId;
    img.src = '/api/edge/' + nodeId + '/snapshot/latest?t=' + Date.now();
    lbUpdateTransform();
    lbUpdateZoom();

    lb.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    lbRefreshTimer = setInterval(function() {
        if (lightboxNodeId) {
            img.src = '/api/edge/' + lightboxNodeId + '/snapshot/latest?t=' + Date.now();
        }
    }, 1000);
}

function closeLightbox() {
    document.getElementById('lightbox').classList.add('hidden');
    document.body.style.overflow = '';
    lightboxNodeId = null;
    if (lbRefreshTimer) { clearInterval(lbRefreshTimer); lbRefreshTimer = null; }
}

function closeLightboxOnBg(event) {
    if (event.target.id === 'lightbox' || event.target.id === 'lightbox-body') {
        closeLightbox();
    }
}

function resetZoom() {
    lbScale = 1; lbPanX = 0; lbPanY = 0;
    lbUpdateTransform(); lbUpdateZoom();
}

function lbUpdateTransform() {
    var img = document.getElementById('lightbox-img');
    if (img) img.style.transform = 'translate(' + lbPanX + 'px, ' + lbPanY + 'px) scale(' + lbScale + ')';
}

function lbUpdateZoom() {
    var el = document.getElementById('lightbox-zoom-level');
    if (el) el.textContent = Math.round(lbScale * 100) + '%';
}

// Wheel zoom
document.addEventListener('wheel', function(e) {
    if (document.getElementById('lightbox').classList.contains('hidden')) return;
    e.preventDefault();
    var delta = e.deltaY > 0 ? 0.9 : 1.1;
    var ns = Math.min(LB_MAX_SCALE, Math.max(LB_MIN_SCALE, lbScale * delta));
    var img = document.getElementById('lightbox-img');
    var rect = img.getBoundingClientRect();
    var cx = e.clientX - rect.left - rect.width / 2;
    var cy = e.clientY - rect.top - rect.height / 2;
    var f = ns / lbScale;
    lbPanX = cx - f * (cx - lbPanX);
    lbPanY = cy - f * (cy - lbPanY);
    lbScale = ns;
    lbUpdateTransform(); lbUpdateZoom();
}, { passive: false });

// Mouse drag
document.addEventListener('mousedown', function(e) {
    if (document.getElementById('lightbox').classList.contains('hidden')) return;
    if (e.target.tagName === 'BUTTON' || e.target.tagName === 'A') return;
    var body = document.getElementById('lightbox-body');
    if (!body.contains(e.target) && e.target !== body) return;
    lbDragging = true;
    lbDragStart = { x: e.clientX - lbPanX, y: e.clientY - lbPanY };
    e.preventDefault();
});
document.addEventListener('mousemove', function(e) {
    if (!lbDragging) return;
    lbPanX = e.clientX - lbDragStart.x;
    lbPanY = e.clientY - lbDragStart.y;
    lbUpdateTransform();
});
document.addEventListener('mouseup', function() { lbDragging = false; });

// Touch pinch zoom + drag
document.addEventListener('touchstart', function(e) {
    if (document.getElementById('lightbox').classList.contains('hidden')) return;
    var body = document.getElementById('lightbox-body');
    if (!body.contains(e.target) && e.target !== body) return;
    if (e.touches.length === 2) {
        lbInitDist = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
        lbInitScale = lbScale;
        e.preventDefault();
    } else if (e.touches.length === 1) {
        lbDragging = true;
        lbDragStart = { x: e.touches[0].clientX - lbPanX, y: e.touches[0].clientY - lbPanY };
    }
}, { passive: false });

document.addEventListener('touchmove', function(e) {
    if (document.getElementById('lightbox').classList.contains('hidden')) return;
    if (e.touches.length === 2) {
        var dist = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
        lbScale = Math.min(LB_MAX_SCALE, Math.max(LB_MIN_SCALE, lbInitScale * (dist / lbInitDist)));
        lbUpdateTransform(); lbUpdateZoom();
        e.preventDefault();
    } else if (e.touches.length === 1 && lbDragging) {
        lbPanX = e.touches[0].clientX - lbDragStart.x;
        lbPanY = e.touches[0].clientY - lbDragStart.y;
        lbUpdateTransform();
        e.preventDefault();
    }
}, { passive: false });

document.addEventListener('touchend', function() { lbDragging = false; });

// ESC to close
document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeLightbox(); });

// ===== Initialization =====
document.addEventListener('DOMContentLoaded', function() {
    // Start snapshot refresh (1fps)
    setInterval(refreshSnapshots, SNAPSHOT_REFRESH_INTERVAL);
    
    // Start stale check
    setInterval(checkNodeStatus, STALE_CHECK_INTERVAL);
    
    // Initial status check
    checkNodeStatus();
    
    // Setup image error handlers
    setupImageErrorHandlers();
    
    // WebSocket is already connected by base.html
    // handleWSMessage has been overridden above

    // Load initial pump node data
    loadPumpNodes();

    console.log('[Monitor] Initialized');
});
