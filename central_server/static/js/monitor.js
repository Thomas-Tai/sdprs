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
                    pump_state: msg.data.pump_state,
                    water_level: msg.data.water_level
                };
            }
            break;
    }
};

// ===== Image Error Handling =====
function setupImageErrorHandlers() {
    document.querySelectorAll('.snapshot-card img').forEach(img => {
        img.onerror = function() {
            console.log(`[Monitor] Image load error for ${img.id}`);
        };
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
    
    console.log('[Monitor] Initialized');
});
