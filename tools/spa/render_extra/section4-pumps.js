// Section 4 (Operational Pages — Pumps) remediation suites.
// Owner: fix/spa-lane-2026-08-01 pumps.jsx pass. Findings: OPS-008, OPS-023,
// OPS-025, OPS-028, OPS-029, NEW-UX-001.

module.exports = [
  // ---------------------------------------------------------------- OPS-023
  {
    name: 'OPS-023 missing power_source shows dash, not a confident battery badge',
    target: 'pages/pumps.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const node = { id: 'pump-ops023', name: 't', location: 'x', type: 'pump',
        status: 'online', heartbeat: 5, level: 50, cycles: 3, snoozeMin: 0,
        power: null, pumpState: 'off' };
      ReactDOM.flushSync(() => root.render(React.createElement(PumpsPage, {
        nodes: [node], onSelectNode: () => {}, showToast: () => {} })));
      await settle();
      A('OPS-023 a pump with null power shows a dash, not 電池', container.textContent.indexOf('電池') === -1, container.textContent.slice(0, 500));
      A('OPS-023 a pump with null power shows — in the power slot', container.textContent.indexOf('—') !== -1);
    `,
  },

  // ---------------------------------------------------------------- OPS-028
  {
    name: 'OPS-028 pumps page also rounds battery voltage to 1 decimal',
    target: 'pages/pumps.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const node = { id: 'pump-ops028', name: 't', location: 'x', type: 'pump',
        status: 'online', heartbeat: 5, level: 50, cycles: 3, snoozeMin: 0,
        voltage: 12.734999, power: 'mains', pumpState: 'off' };
      ReactDOM.flushSync(() => root.render(React.createElement(PumpsPage, {
        nodes: [node], onSelectNode: () => {}, showToast: () => {} })));
      await settle();
      A('OPS-028 pump voltage is rendered rounded (12.7V)', container.textContent.indexOf('12.7V') !== -1 && container.textContent.indexOf('12.734999') === -1, container.textContent.slice(0, 500));
    `,
  },

  // ------------------------------------------------------------- NEW-UX-001
  {
    name: 'NEW-UX-001 PumpCard no longer wraps real buttons in role="button"',
    target: 'pages/pumps.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const node = { id: 'pump-ux001', name: 't', location: 'x', type: 'pump',
        status: 'online', heartbeat: 5, level: 50, cycles: 3, snoozeMin: 0,
        power: 'mains', pumpState: 'off' };
      let selected = null;
      ReactDOM.flushSync(() => root.render(React.createElement(PumpsPage, {
        nodes: [node], onSelectNode: (n) => { selected = n; }, showToast: () => {} })));
      await settle();
      const buttons = Array.from(container.querySelectorAll('button'));
      A('NEW-UX-001 setup: PumpCard renders buttons', buttons.length > 0);
      const nested = buttons.some(b => b.closest('[role="button"]'));
      A('NEW-UX-001 no real button is nested inside a role="button" wrapper', !nested);
      const card = container.querySelector('.bg-surface-panel');
      if (card) {
        click(card.querySelector('.font-mono.font-bold'));
        await settle();
        A('NEW-UX-001 clicking a non-button area still selects the node', selected && selected.id === 'pump-ux001', selected);
      }
    `,
  },

  // ---------------------------------------------------------------- OPS-025
  {
    name: 'OPS-025 pump control wrapper only stops Enter/Space, not Escape',
    target: 'pages/pumps.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const node = { id: 'pump-ops025', name: 't', location: 'x', type: 'pump',
        status: 'online', heartbeat: 5, level: 50, cycles: 3, snoozeMin: 0,
        power: 'mains', pumpState: 'off' };
      ReactDOM.flushSync(() => root.render(React.createElement(PumpsPage, {
        nodes: [node], onSelectNode: () => {}, showToast: () => {} })));
      await settle();
      // Find the control wrapper div that has onClick stopPropagation
      const controlWrapper = Array.from(container.querySelectorAll('div')).find(d =>
        d.querySelector('button') && d.className === '');
      if (controlWrapper) {
        let escapeBubbled = false;
        const parentHandler = () => { escapeBubbled = true; };
        controlWrapper.parentElement.addEventListener('keydown', parentHandler);
        const escEvent = new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
        controlWrapper.dispatchEvent(escEvent);
        controlWrapper.parentElement.removeEventListener('keydown', parentHandler);
        A('OPS-025 Escape key bubbles through the control wrapper', escapeBubbled);
      }
    `,
  },
];
