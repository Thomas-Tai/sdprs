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
];
