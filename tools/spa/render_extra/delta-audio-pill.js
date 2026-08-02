// delta-audit remediation suite.
// Finding: NEW-UX-014 — the audio-armed pill inside StatusStrip was
// `hidden md:inline-flex`, so mobile operators (below the `md` breakpoint)
// had no visible armed/unarmed indicator and no manual re-arm control if the
// auto-arm-on-first-gesture listener failed. Fix makes it a compact
// icon-only badge on mobile (always visible) that expands to the full
// emoji+text pill at `md` and above, with an explicit aria-label since the
// text collapses out of the accessible name on narrow widths.
module.exports = [
  {
    name: 'NEW-UX-014 StatusStrip audio pill is visible below md (unarmed)',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      window.SDPRS_AUDIO = { isAvailable: () => true, isArmed: () => false, arm: () => {} };
      const noop = () => {};
      const props = { unackCount: 0, muted: false, theme: 'dark', setTheme: noop,
        onOpenShortcuts: noop, setPage: noop, onOpenMuteDrawer: noop, audioReplayIn: 0,
        muteState: { nodes: [], sources: [], global: false }, operators: [], staleAckCount: 0,
        onOpenCmdK: noop, focusMode: false, onToggleFocus: noop };
      ReactDOM.flushSync(() => root.render(React.createElement(window.StatusStrip, props)));
      await settle();
      const pill = Array.from(container.querySelectorAll('button')).find(b => (b.title || '').indexOf('音效') !== -1 && (b.title || '').indexOf('啟用') !== -1);
      A('NEW-UX-014 setup: audio-armed pill button renders', !!pill, container.innerHTML.slice(0, 300));

      A('NEW-UX-014 (unarmed) pill className does NOT contain the "hidden" token (visible on mobile)',
        !!pill && pill.className.split(/\\s+/).indexOf('hidden') === -1, pill && pill.className);
      A('NEW-UX-014 (unarmed) pill has a non-empty aria-label',
        !!pill && !!pill.getAttribute('aria-label') && pill.getAttribute('aria-label').length > 0,
        pill && pill.getAttribute('aria-label'));
      const collapsibleSpan = pill && Array.from(pill.querySelectorAll('span')).find(s => s.className.indexOf('hidden') !== -1 && s.className.indexOf('md:inline') !== -1);
      A('NEW-UX-014 (unarmed) pill contains a child span with className "hidden md:inline" (text collapses on mobile)',
        !!collapsibleSpan, pill && pill.innerHTML);
      A('NEW-UX-014 (unarmed) emoji glyph 🔇 is present in the rendered text',
        !!pill && pill.textContent.indexOf('🔇') !== -1, pill && pill.textContent);
    `,
  },
  {
    name: 'NEW-UX-014 StatusStrip audio pill is visible below md (armed)',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      window.SDPRS_AUDIO = { isAvailable: () => true, isArmed: () => true, arm: () => {} };
      const noop = () => {};
      const props = { unackCount: 0, muted: false, theme: 'dark', setTheme: noop,
        onOpenShortcuts: noop, setPage: noop, onOpenMuteDrawer: noop, audioReplayIn: 0,
        muteState: { nodes: [], sources: [], global: false }, operators: [], staleAckCount: 0,
        onOpenCmdK: noop, focusMode: false, onToggleFocus: noop };
      ReactDOM.flushSync(() => root.render(React.createElement(window.StatusStrip, props)));
      await settle();
      const pill = Array.from(container.querySelectorAll('button')).find(b => (b.title || '').indexOf('音效') !== -1 && (b.title || '').indexOf('已啟用') !== -1);
      A('NEW-UX-014 setup: armed audio pill button renders', !!pill, container.innerHTML.slice(0, 300));

      A('NEW-UX-014 (armed) pill className does NOT contain the "hidden" token (visible on mobile)',
        !!pill && pill.className.split(/\\s+/).indexOf('hidden') === -1, pill && pill.className);
      A('NEW-UX-014 (armed) pill has a non-empty aria-label',
        !!pill && !!pill.getAttribute('aria-label') && pill.getAttribute('aria-label').length > 0,
        pill && pill.getAttribute('aria-label'));
      const collapsibleSpan = pill && Array.from(pill.querySelectorAll('span')).find(s => s.className.indexOf('hidden') !== -1 && s.className.indexOf('md:inline') !== -1);
      A('NEW-UX-014 (armed) pill contains a child span with className "hidden md:inline" (text collapses on mobile)',
        !!collapsibleSpan, pill && pill.innerHTML);
      A('NEW-UX-014 (armed) emoji glyph 🔊 is present in the rendered text',
        !!pill && pill.textContent.indexOf('🔊') !== -1, pill && pill.textContent);
    `,
  },
];
