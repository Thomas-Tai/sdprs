// Node delete + hide feature suites (2026-08-22).
// One file per owner (see render_tests.js loadExtraSuites) so the shared
// render_tests.js SUITES array takes no collisions. Each entry targets its
// own file; `body` runs with the PRELUDE helpers (A, settle, click, byText,
// setInput, container, root, React, ReactDOM) plus the target file internals.
module.exports = [
  // ---------------------------------------- data.jsx: hide storage + filter
  {
    name: 'node-hide data.jsx: loadHiddenNodes / saveHiddenNodes / filterVisibleNodes',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      // Round-trips through the REAL jsdom localStorage.
      try { window.localStorage.removeItem('sdprs.hiddenNodes'); } catch (e) {}
      A('empty store loads as []', Array.isArray(loadHiddenNodes()) && loadHiddenNodes().length === 0);

      saveHiddenNodes(['CAM-9', 'pump-3']);
      const loaded = loadHiddenNodes();
      A('saved ids round-trip back', loaded.length === 2 && loaded.indexOf('CAM-9') !== -1 && loaded.indexOf('pump-3') !== -1, JSON.stringify(loaded));
      A('the raw value is JSON in the expected key', window.localStorage.getItem('sdprs.hiddenNodes') === JSON.stringify(['CAM-9','pump-3']), window.localStorage.getItem('sdprs.hiddenNodes'));

      // Corrupt payload degrades to [] instead of throwing.
      window.localStorage.setItem('sdprs.hiddenNodes', 'not-json{');
      A('a corrupt payload loads as []', loadHiddenNodes().length === 0);
      window.localStorage.setItem('sdprs.hiddenNodes', JSON.stringify({ not: 'an array' }));
      A('a non-array payload loads as []', loadHiddenNodes().length === 0);

      // A throwing store (private window / blocked storage) degrades, not crashes.
      const realLS = window.localStorage;
      Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('SecurityError'); } });
      A('a throwing localStorage.load degrades to []', loadHiddenNodes().length === 0);
      let threw = false;
      try { saveHiddenNodes(['x']); } catch (e) { threw = true; }
      A('a throwing localStorage.save is a silent no-op (never throws)', threw === false);
      Object.defineProperty(window, 'localStorage', { configurable: true, value: realLS });

      // filterVisibleNodes: drops hidden ids, passes the rest through.
      const nodes = [{ id: 'CAM-1' }, { id: 'CAM-9' }, { id: 'pump-3' }];
      const hidden = new Set(['CAM-9', 'pump-3']);
      const vis = filterVisibleNodes(nodes, hidden);
      A('filterVisibleNodes removes hidden ids', vis.length === 1 && vis[0].id === 'CAM-1', JSON.stringify(vis));
      A('filterVisibleNodes with an empty set returns all', filterVisibleNodes(nodes, new Set()).length === 3);
      A('filterVisibleNodes with null hiddenIds returns all', filterVisibleNodes(nodes, null).length === 3);
      A('filterVisibleNodes null-safe on non-array nodes', filterVisibleNodes(null, hidden).length === 0);
    `,
  },
];
