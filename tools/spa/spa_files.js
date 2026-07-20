// Shared config for the SPA gates: where the SPA lives, and the file list in
// index.html's <script> order. Load order matters — a file can only reference
// what EARLIER files published to window (see scope_probe.js).
const path = require('path');

const SPA_DIR = path.resolve(__dirname, '..', '..', 'central_server', 'static', 'spa');

const SPA_FILES = [
  'icons.jsx', 'data.jsx', 'api.jsx', 'components.jsx',
  'pages/alerts.jsx', 'pages/monitor.jsx', 'pages/status.jsx',
  'pages/weather.jsx', 'pages/handover.jsx', 'pages/audit.jsx',
  'pages/pumps.jsx', 'tweaks-panel.jsx', 'app.jsx',
];

module.exports = { SPA_DIR, SPA_FILES };
