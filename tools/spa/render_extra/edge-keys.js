// render_extra suite — Track 1 "per-node edge API keys", Task 10: SPA
// api.jsx wiring for per-node edge key provisioning.
//
// Backend (Tasks 1-9) is live:
//   POST   /api/nodes/{node_id}/key -> 201 { node_id, api_key }  (raw key, shown once)
//   DELETE /api/nodes/{node_id}/key -> 204 (404 if no key)
//   GET    /api/nodes now sends a `has_key` field per node (bool for
//          glass/pump; null for webcam rows).
//
// This suite drives the new window.SDPRS_API.provisionNodeKey /
// .clearNodeKey functions (mirroring the existing webcam-key siblings
// createWebcamClient/revokeWebcamKey/deleteWebcamClient) and asserts that
// mapNode surfaces the wire's `has_key` as camelCase `hasKey` — Task 11's
// node-key panel gates its controls on `node.hasKey`.

const RES_HELPER = `
  const _res = (data, opts) => {
    const o = opts || {};
    const status = o.status != null ? o.status : 200;
    const ct = o.ct != null ? o.ct : 'application/json';
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      headers: { get: (k) => (String(k).toLowerCase() === 'content-type' ? ct : null) },
      json: () => Promise.resolve(data),
      text: () => Promise.resolve(typeof data === 'string' ? data : JSON.stringify(data)),
    });
  };
`;

module.exports = [
  // -------------------------------------------- api.jsx: provisionNodeKey --
  {
    name: 'EDGE-KEYS-001              api.jsx (provisionNodeKey POST /key)',
    target: 'api.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      ${RES_HELPER}
      let capturedPath = null, capturedOpts = null;
      window.fetch = (path, opts) => {
        capturedPath = String(path); capturedOpts = opts;
        return _res({ node_id: 'n1', api_key: 'sk-edge-xyz' });
      };
      A('provisionNodeKey is published on SDPRS_API', typeof window.SDPRS_API.provisionNodeKey === 'function', typeof window.SDPRS_API.provisionNodeKey);
      const result = await window.SDPRS_API.provisionNodeKey('n1');
      A('provisionNodeKey POSTs /api/nodes/n1/key', capturedPath === '/api/nodes/n1/key', capturedPath);
      A('provisionNodeKey uses method POST', !!capturedOpts && capturedOpts.method === 'POST', capturedOpts && capturedOpts.method);
      A('provisionNodeKey returns the stubbed { api_key }', !!result && result.api_key === 'sk-edge-xyz', JSON.stringify(result));

      // encodeURIComponent contract — mirrors DATA-018's webcam-key siblings.
      const raw = 'n 1/x?y';
      const enc = encodeURIComponent(raw);
      await window.SDPRS_API.provisionNodeKey(raw);
      A('provisionNodeKey encodeURIComponents the node id', capturedPath === '/api/nodes/' + enc + '/key', capturedPath);
    `,
  },

  // ------------------------------------------------- api.jsx: clearNodeKey --
  {
    name: 'EDGE-KEYS-002              api.jsx (clearNodeKey DELETE /key)',
    target: 'api.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      ${RES_HELPER}
      let capturedPath = null, capturedOpts = null;
      window.fetch = (path, opts) => {
        capturedPath = String(path); capturedOpts = opts;
        return _res(null, { status: 204 });
      };
      A('clearNodeKey is published on SDPRS_API', typeof window.SDPRS_API.clearNodeKey === 'function', typeof window.SDPRS_API.clearNodeKey);
      await window.SDPRS_API.clearNodeKey('n1');
      A('clearNodeKey sends DELETE to /api/nodes/n1/key', capturedPath === '/api/nodes/n1/key', capturedPath);
      A('clearNodeKey uses method DELETE', !!capturedOpts && capturedOpts.method === 'DELETE', capturedOpts && capturedOpts.method);

      const raw = 'n 1/x?y';
      const enc = encodeURIComponent(raw);
      await window.SDPRS_API.clearNodeKey(raw);
      A('clearNodeKey encodeURIComponents the node id', capturedPath === '/api/nodes/' + enc + '/key', capturedPath);
    `,
  },

  // --------------------------------------- api.jsx: mapNode surfaces hasKey --
  {
    name: 'EDGE-KEYS-003              api.jsx (mapNode surfaces hasKey)',
    target: 'api.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      ${RES_HELPER}
      // Glass node WITH a provisioned key.
      const keyedGlass = { node_id: 'g1', node_type: 'glass', status: 'ONLINE', has_key: true };
      // Glass node with NO key — must map to false (a real negative), never
      // be coerced to null/undefined/dropped by the whitelist mapper.
      const unkeyedGlass = { node_id: 'g2', node_type: 'glass', status: 'ONLINE', has_key: false };
      // Webcam row — backend sends has_key: null (key concept doesn't apply).
      const webcamRow = { node_id: 'webcam_a1', node_type: 'webcam', status: 'ONLINE', has_key: null };
      window.fetch = (path) => {
        if (String(path).indexOf('/api/nodes') === 0) return _res([keyedGlass, unkeyedGlass, webcamRow]);
        return _res([]);
      };
      const rl = await window.SDPRS_API.refreshLive();
      const g1 = (rl.nodes || []).find(n => n.id === 'g1');
      const g2 = (rl.nodes || []).find(n => n.id === 'g2');
      const wc = (rl.nodes || []).find(n => n.id === 'webcam_a1');
      A('mapNode surfaces hasKey=true for a keyed glass node', !!g1 && g1.hasKey === true, g1 && String(g1.hasKey));
      A('mapNode surfaces hasKey=false for an unkeyed glass node', !!g2 && g2.hasKey === false, g2 && String(g2.hasKey));
      A('mapNode surfaces hasKey=null for a webcam row', !!wc && wc.hasKey === null, wc && String(wc.hasKey));
    `,
  },
];
