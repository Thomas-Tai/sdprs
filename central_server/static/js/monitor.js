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
    // Update location
    const locEl = document.getElementById(`location-${node.node_id}`);

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

    // Update location display
    if (locEl && node.location) {
        locEl.textContent = node.location;
        locEl.title = node.location;
        locEl.style.display = '';
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
    // API uses node_type; WebSocket messages use type — normalise
    const nodeType = data.node_type || data.type;
    const dotClass = isOnline ? 'bg-green-500' : 'bg-red-500';
    // Location from nodeStates (enriched by /api/nodes fetch)
    const location = data.location || '';

    // Hide empty placeholder
    const emptyEl = document.getElementById('pump-nodes-empty');
    if (emptyEl) emptyEl.classList.add('hidden');

    let card = document.getElementById('pump-card-' + nodeId);
    if (!card) {
        // Create new card
        card = document.createElement('div');
        card.className = 'bg-white rounded-lg shadow p-4';
        card.id = 'pump-card-' + nodeId;
        card.dataset.nodeType = 'pump';
        card.innerHTML = `
            <div class="flex justify-between items-center mb-1">
                <div class="flex items-center gap-2 min-w-0">
                    <span class="font-mono text-sm font-bold">${nodeId}</span>
                    <span class="location-label text-xs text-gray-500 truncate max-w-[100px]" id="pump-location-${nodeId}" title="${location}">${location}</span>
                    <button class="edit-location-btn text-gray-400 hover:text-yellow-400 text-xs p-0.5" onclick="openLocationModal('${nodeId}', 'pump')" title="編輯地點">✏️</button>
                </div>
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
            <div class="flex justify-between items-center mt-2">
                <span class="text-xs text-gray-400">更新: <span id="pump-last-update-${nodeId}">--</span></span>
                <button onclick="openPumpHistoryModal('${nodeId}')"
                        class="text-xs text-blue-600 hover:text-blue-800 underline">查看歷史</button>
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

        const locEl = document.getElementById('pump-location-' + nodeId);
        if (locEl && location) {
            locEl.textContent = location;
            locEl.title = location;
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
            if (node.node_type === 'pump') {
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
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeLightbox();
        closeLocationModal();
        closePumpHistoryModal();
    }
});

// ===== Location Edit Modal =====
let locationModalNodeId = null;
let locationModalNodeType = null;

function openLocationModal(nodeId, nodeType) {
    locationModalNodeId = nodeId;
    locationModalNodeType = nodeType;
    document.getElementById('location-modal-node-id').textContent = nodeId;

    // Get current location from nodeStates or DOM
    let currentLocation = '';
    if (nodeType === 'pump') {
        const locEl = document.getElementById('pump-location-' + nodeId);
        currentLocation = locEl ? locEl.textContent.trim() : '';
    } else {
        const locEl = document.getElementById('location-' + nodeId);
        currentLocation = locEl ? locEl.textContent.trim() : '';
    }
    // If it's italic placeholder text, clear it
    if (currentLocation && !nodeStates[nodeId]?.location) {
        currentLocation = '';
    }
    document.getElementById('location-input').value = nodeStates[nodeId]?.location || currentLocation;

    document.getElementById('location-modal').classList.remove('hidden');
    document.getElementById('location-input').focus();
}

function closeLocationModal() {
    document.getElementById('location-modal').classList.add('hidden');
    locationModalNodeId = null;
    locationModalNodeType = null;
}

function saveLocation() {
    if (!locationModalNodeId) return;

    const newLocation = document.getElementById('location-input').value.trim();

    fetch('/api/nodes/' + locationModalNodeId, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location: newLocation })
    })
    .then(r => r.json())
    .then(data => {
        // Update nodeStates
        if (nodeStates[locationModalNodeId]) {
            nodeStates[locationModalNodeId].location = newLocation;
        }

        // Update DOM
        if (locationModalNodeType === 'pump') {
            const locEl = document.getElementById('pump-location-' + locationModalNodeId);
            if (locEl) {
                locEl.textContent = newLocation;
                locEl.title = newLocation;
            }
        } else {
            const locEl = document.getElementById('location-' + locationModalNodeId);
            if (locEl) {
                locEl.textContent = newLocation;
                locEl.title = newLocation;
                locEl.style.display = newLocation ? '' : 'none';
            }
        }

        closeLocationModal();
    })
    .catch(err => {
        console.error('[Monitor] Failed to save location:', err);
        alert('保存失敗，請稍後重試');
    });
}

// ===== Pump History Chart Modal =====
let pumpHistoryNodeId = null;
let pumpHistoryChart = null;

function openPumpHistoryModal(nodeId) {
    pumpHistoryNodeId = nodeId;
    document.getElementById('pump-history-node-id').textContent = nodeId;

    // Reset time range buttons
    document.querySelectorAll('.time-range-btn').forEach(btn => btn.classList.remove('bg-blue-600', 'text-white'));
    document.getElementById('pump-history-start').value = '';
    document.getElementById('pump-history-end').value = '';
    document.getElementById('pump-history-empty').classList.add('hidden');

    // Show modal
    document.getElementById('pump-history-modal').classList.remove('hidden');

    // Load default 1 hour
    setPumpTimeRange('1h');
}

function closePumpHistoryModal() {
    document.getElementById('pump-history-modal').classList.add('hidden');
    if (pumpHistoryChart) {
        pumpHistoryChart.destroy();
        pumpHistoryChart = null;
    }
    pumpHistoryNodeId = null;
}

function setPumpTimeRange(range) {
    const now = new Date();
    let start = new Date();

    switch (range) {
        case '1h': start.setHours(now.getHours() - 1); break;
        case '6h': start.setHours(now.getHours() - 6); break;
        case '24h': start.setDate(now.getDate() - 1); break;
        case '7d': start.setDate(now.getDate() - 7); break;
    }

    // Highlight button
    document.querySelectorAll('.time-range-btn').forEach(btn => {
        if (btn.dataset.range === range) {
            btn.classList.add('bg-blue-600', 'text-white');
        } else {
            btn.classList.remove('bg-blue-600', 'text-white');
        }
    });

    loadPumpHistory(start, now);
}

function loadPumpHistoryCustom() {
    const startStr = document.getElementById('pump-history-start').value;
    const endStr = document.getElementById('pump-history-end').value;

    if (!startStr || !endStr) {
        alert('請選擇開始與結束時間');
        return;
    }

    const start = new Date(startStr);
    const end = new Date(endStr);

    if (start >= end) {
        alert('開始時間需早於結束時間');
        return;
    }

    // Clear button highlights
    document.querySelectorAll('.time-range-btn').forEach(btn => btn.classList.remove('bg-blue-600', 'text-white'));

    loadPumpHistory(start, end);
}

function loadPumpHistory(start, end) {
    if (!pumpHistoryNodeId) return;

    const startIso = start.toISOString();
    const endIso = end.toISOString();

    fetch('/api/pump/' + pumpHistoryNodeId + '/history?start=' + encodeURIComponent(startIso) + '&end=' + encodeURIComponent(endIso))
    .then(r => r.json())
    .then(data => {
        renderPumpHistoryChart(data);
    })
    .catch(err => {
        console.error('[Monitor] Failed to load pump history:', err);
        document.getElementById('pump-history-empty').classList.remove('hidden');
        if (pumpHistoryChart) {
            pumpHistoryChart.destroy();
            pumpHistoryChart = null;
        }
    });
}

function renderPumpHistoryChart(data) {
    const canvas = document.getElementById('pump-history-chart');
    const emptyEl = document.getElementById('pump-history-empty');

    if (!data || data.length === 0) {
        emptyEl.classList.remove('hidden');
        if (pumpHistoryChart) {
            pumpHistoryChart.destroy();
            pumpHistoryChart = null;
        }
        return;
    }

    emptyEl.classList.add('hidden');

    // Prepare data
    const labels = data.map(row => {
        const d = new Date(row.timestamp);
        return d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' });
    });
    const waterLevels = data.map(row => row.water_level);

    // Destroy old chart if exists
    if (pumpHistoryChart) {
        pumpHistoryChart.destroy();
    }

    // Create new chart
    pumpHistoryChart = new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: '雨量感測 (%)',
                data: waterLevels,
                borderColor: 'rgb(59, 130, 246)',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: true, position: 'top' },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            return '水位: ' + (ctx.raw !== null ? ctx.raw.toFixed(1) + '%' : '--');
                        }
                    }
                }
            },
            scales: {
                x: {
                    display: true,
                    title: { display: false }
                },
                y: {
                    display: true,
                    min: 0,
                    max: 100,
                    title: { display: true, text: '水位 (%)' }
                }
            }
        }
    });
}

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
