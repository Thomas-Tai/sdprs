// Section 3 (Shared Components & Tweaks Panel) remediation suites.
// Owner: fix/spa-lane-2026-08-01 Section 3 pass. See
// docs/audits/full_dashboard_audit_2026-07-26.md, Section 3, for the COMP-0xx
// finding numbers referenced below.
module.exports = [
  {
    name: 'COMP-002 ShortcutsModal restores focus to the real pre-open trigger',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // A detached autoFocus input used to be captured as "the element to
      // restore focus to" (because autoFocus steals focus during commit,
      // BEFORE the capture effect ran) — restoring focus to it after it
      // unmounts is a no-op, so focus fell through to <body>.
      const trigger = document.createElement('button');
      trigger.textContent = 'open shortcuts trigger';
      document.body.appendChild(trigger);
      trigger.focus();
      A('COMP-002 setup: trigger has focus before modal opens', document.activeElement === trigger);

      const render = (open) => ReactDOM.flushSync(() => root.render(
        React.createElement(window.ShortcutsModal, { open, onClose: () => {} })
      ));
      render(true);
      await settle();
      render(false);
      await settle();
      A('COMP-002 ShortcutsModal restores focus to the pre-open trigger, not <body>',
        document.activeElement === trigger,
        document.activeElement && document.activeElement.tagName);
      document.body.removeChild(trigger);
    `,
  },
  {
    name: 'COMP-002 CommandPalette restores focus to the real pre-open trigger',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const trigger = document.createElement('button');
      trigger.textContent = 'open cmdk trigger';
      document.body.appendChild(trigger);
      trigger.focus();
      A('COMP-002 setup: trigger has focus before palette opens', document.activeElement === trigger);

      const render = (open) => ReactDOM.flushSync(() => root.render(
        React.createElement(window.CommandPalette, {
          open, onClose: () => {}, alerts: [], nodes: [],
          onSelectAlert: () => {}, onNav: () => {}, onCmd: () => {},
        })
      ));
      render(true);
      await settle();
      render(false);
      await settle();
      A('COMP-002 CommandPalette restores focus to the pre-open trigger, not <body>',
        document.activeElement === trigger,
        document.activeElement && document.activeElement.tagName);
      document.body.removeChild(trigger);
    `,
  },
  {
    name: 'COMP-003 MuteDrawer: Shift+Tab from the heading wraps to the last control (does not escape)',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // FOCUSABLE_SELECTOR / trapTab are file-internal (not published to
      // window) — reachable here because this suite's target IS
      // components.jsx, so it shares scope with the file under test.
      let muteState = { nodes: [], sources: [], global: false, lightning: false, volume: 70 };
      const setMuteState = (fn) => { muteState = typeof fn === 'function' ? fn(muteState) : fn; };
      ReactDOM.flushSync(() => root.render(React.createElement(window.MuteDrawer, {
        open: true, onClose: () => {}, muteState, setMuteState, nodes: [],
      })));
      await settle();
      const heading = container.querySelector('h2');
      A('COMP-003 setup: MuteDrawer heading has initial focus', document.activeElement === heading);

      const focusable = Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR));
      const lastEl = focusable[focusable.length - 1];
      A('COMP-003 setup: at least one real focusable control exists', !!lastEl);

      const ev = new window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
      window.dispatchEvent(ev);
      await settle();
      A('COMP-003 MuteDrawer Shift+Tab from the heading wraps to the last control, not out of the modal',
        document.activeElement === lastEl,
        document.activeElement && (document.activeElement.tagName + '.' + document.activeElement.className));
    `,
  },
  {
    name: 'COMP-003 NodeSidePanel: Shift+Tab from the heading wraps to the last control (does not escape)',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const node = {
        id: 'PUMP-01', name: '測試泵浦', location: '3F · 西側', status: 'online',
        type: 'pump', heartbeat: 5, upload: 5, level: 40, cycles: 0,
      };
      ReactDOM.flushSync(() => root.render(React.createElement(window.NodeSidePanel, {
        node, history: [], onClose: () => {}, onJumpAlert: () => {}, onNavigate: () => {},
        onSelectAlert: () => {}, openAlerts: [], onUpdateNode: () => Promise.resolve(),
      })));
      await settle();
      const heading = container.querySelector('h2');
      A('COMP-003 setup: NodeSidePanel heading has initial focus', document.activeElement === heading);

      const focusable = Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR));
      const lastEl = focusable[focusable.length - 1];
      A('COMP-003 setup: at least one real focusable control exists', !!lastEl);

      const ev = new window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
      window.dispatchEvent(ev);
      await settle();
      A('COMP-003 NodeSidePanel Shift+Tab from the heading wraps to the last control, not out of the modal',
        document.activeElement === lastEl,
        document.activeElement && (document.activeElement.tagName + '.' + document.activeElement.className));
    `,
  },
];
