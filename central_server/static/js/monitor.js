/**
 * SDPRS Monitor Wall JavaScript
 * Smart Disaster Prevention Response System
 * 
 * Real-time camera snapshot grid with:
 * - 1fps auto-refresh
 * - Stale detection (>10s without update)
 * - Offline detection
 * - WebSocket status updates
 */

// ===== Configuration =====
const SNAPSHOT_REFRESH_INTERVAL = 1000;  // 1 second
const STALE_CHECK_INTERVAL = 5000;       // 5 seconds
const STALE_THRESHOLD = 10;              // 10 seconds
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
const RECONNECT_DELAY = 3000;

// ===== State =====
let ws = null;
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

// ===== WebSocket Connection =====
function connectWebSocket() {
    ws = new WebSocket(WS_URL);
    
    ws.onopen = function() {
        console.log('[Monitor WS] Connected');
    };
    
    ws.onmessage = function(event) {
        try {
            const msg = JSON.parse(event.data);
            handleWSMessage(msg);
        } catch (e) {
            console.error('[Monitor WS] Parse error:', e);
        }
    };
    
    ws.onclose = function() {
        console.log('[Monitor WS] Disconnected');
        setTimeout(connectWebSocket, RECONNECT_DELAY);
    };
    
    ws.onerror = function(error) {
        console.error('[Monitor WS] Error:', error);
    };
}

function handleWSMessage(msg) {
    switch (msg.type) {
        case 'node_status':
            // Update specific node
            if (msg.data && msg.data.node_id) {
                nodeStates[msg.data.node_id] = {
                    ...nodeStates[msg.data.node_id],
                    ...msg.data
                };
                updateNodeCard(nodeStates[msg.data.node_id]);
            }
            break;
            
        case 'pump_status':
            // Update pump node
            if (msg.data && msg.data.node_id) {
                nodeStates[msg.data.node_id] = {
                    ...nodeStates[msg.data.node_id],
                    node_id: msg.data.node_id,
                    pump_state: msg.data.pump_state,
                    water_level: msg.data.water_level
                };
            }
            break;
    }
}

// ===== Image Error Handling =====
function setupImageErrorHandlers() {
    document.querySelectorAll('.snapshot-card img').forEach(img => {
        img.onerror = function() {
            // On error, the server returns a placeholder image
            // Just retry the same URL
            console.log(`[Monitor] Image load error for ${img.id}`);
        };
    });
}

// ===== Utility Functions =====
function formatTimestamp(isoString) {
    if (!isoString) return '-';
    
    try {
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffSec = Math.floor(diffMs / 1000);
        
        // Show relative time for recent updates
        if (diffSec < 60) {
            return `${diffSec}秒前`;
        } else if (diffSec < 3600) {
            return `${Math.floor(diffSec / 60)}分鐘前`;
        } else {
            return date.toLocaleString('zh-TW', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            });
        }
    } catch (e) {
        return isoString;
    }
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
    
    // Connect WebSocket
    connectWebSocket();
    
    console.log('[Monitor] Initialized');
});

// Export for global access
window.formatTimestamp = formatTimestamp;