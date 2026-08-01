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
  {
    name: 'COMP-004 HlsPlayer retry budget resets when nodeId changes',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // Minimal hls.js double: a plain constructor function (not an ES class —
      // this test body is Babel-transformed with the 'react' preset only) that
      // records every instance so the test can drive its ERROR event by hand.
      const instances = [];
      function FakeHls(opts) {
        this.opts = opts;
        this.handlers = {};
        this.destroyed = false;
        instances.push(this);
      }
      FakeHls.isSupported = () => true;
      FakeHls.Events = { MANIFEST_PARSED: 'hlsManifestParsed', ERROR: 'hlsError' };
      FakeHls.ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError', OTHER_ERROR: 'otherError' };
      FakeHls.prototype.loadSource = function (src) { this.src = src; };
      FakeHls.prototype.attachMedia = function (video) { this.video = video; };
      FakeHls.prototype.on = function (evt, cb) { this.handlers[evt] = cb; };
      FakeHls.prototype.trigger = function (evt, data) { if (this.handlers[evt]) this.handlers[evt](evt, data); };
      FakeHls.prototype.startLoad = function () { this.startLoadCalls = (this.startLoadCalls || 0) + 1; };
      FakeHls.prototype.recoverMediaError = function () { this.recoverCalls = (this.recoverCalls || 0) + 1; };
      FakeHls.prototype.destroy = function () { this.destroyed = true; };
      window.Hls = FakeHls;

      const proto = window.HTMLMediaElement.prototype;
      const origPlay = proto.play;
      proto.play = () => Promise.resolve();

      let fellBack = 0;
      const render = (nodeId) => ReactDOM.flushSync(() => root.render(
        React.createElement(window.HlsPlayer, { nodeId, onFallback: () => { fellBack++; } })
      ));

      render('node-a');
      await settle();
      A('COMP-004 setup: mounting creates one Hls instance', instances.length === 1, instances.length);
      const first = instances[0];
      // Two fatal errors on node-a: under budget (3), no fallback yet.
      first.trigger(FakeHls.Events.ERROR, { fatal: true, type: FakeHls.ErrorTypes.MEDIA_ERROR });
      first.trigger(FakeHls.Events.ERROR, { fatal: true, type: FakeHls.ErrorTypes.MEDIA_ERROR });
      await settle();
      A('COMP-004 setup: 2 fatal errors on node-a do not yet trigger fallback', fellBack === 0, fellBack);

      // Switch to a different node — a brand new stream session. The retry
      // budget must NOT carry over the 2 strikes already used against node-a.
      render('node-b');
      await settle();
      A('COMP-004 setup: switching nodeId destroys the old hls instance', first.destroyed === true);
      A('COMP-004 setup: switching nodeId creates a fresh Hls instance', instances.length === 2, instances.length);
      const second = instances[1];

      fellBack = 0;
      // Only ONE fatal error on the fresh node-b session — must NOT hit the
      // fallback threshold (that would only be correct if the budget had
      // incorrectly carried over node-a's 2 prior strikes).
      second.trigger(FakeHls.Events.ERROR, { fatal: true, type: FakeHls.ErrorTypes.MEDIA_ERROR });
      await settle();
      A('COMP-004 a fresh node gets a fresh retry budget (1 error does not force permanent fallback)',
        fellBack === 0, 'fellBack=' + fellBack);

      proto.play = origPlay;
      delete window.Hls;
    `,
  },
  {
    name: 'COMP-005 HlsPlayer uses hls.js own recovery action per error type',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // recoverMediaError() only repairs the MSE/media pipeline — it does
      // nothing for a failed network fetch (the manifest 404ing/timing out,
      // i.e. "hls.js fails to load"). Calling it for a network-type fatal
      // error left the loader permanently stalled: hls.js never fired another
      // ERROR event, so the retry counter never reached the fallback
      // threshold and the tile stayed a silent, unexplained black frame.
      const instances = [];
      function FakeHls(opts) {
        this.opts = opts;
        this.handlers = {};
        this.destroyed = false;
        instances.push(this);
      }
      FakeHls.isSupported = () => true;
      FakeHls.Events = { MANIFEST_PARSED: 'hlsManifestParsed', ERROR: 'hlsError' };
      FakeHls.ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError', OTHER_ERROR: 'otherError' };
      FakeHls.prototype.loadSource = function (src) { this.src = src; };
      FakeHls.prototype.attachMedia = function (video) { this.video = video; };
      FakeHls.prototype.on = function (evt, cb) { this.handlers[evt] = cb; };
      FakeHls.prototype.trigger = function (evt, data) { if (this.handlers[evt]) this.handlers[evt](evt, data); };
      FakeHls.prototype.startLoad = function () { this.startLoadCalls = (this.startLoadCalls || 0) + 1; };
      FakeHls.prototype.recoverMediaError = function () { this.recoverCalls = (this.recoverCalls || 0) + 1; };
      FakeHls.prototype.destroy = function () { this.destroyed = true; };
      window.Hls = FakeHls;

      const proto = window.HTMLMediaElement.prototype;
      const origPlay = proto.play;
      proto.play = () => Promise.resolve();

      const render = (nodeId) => ReactDOM.flushSync(() => root.render(
        React.createElement(window.HlsPlayer, { nodeId, onFallback: () => {} })
      ));

      render('net-node');
      await settle();
      const netHls = instances[0];
      netHls.trigger(FakeHls.Events.ERROR, { fatal: true, type: FakeHls.ErrorTypes.NETWORK_ERROR });
      await settle();
      A('COMP-005 a fatal NETWORK_ERROR calls hls.startLoad() (the correct network-recovery action)',
        (netHls.startLoadCalls || 0) === 1, 'startLoadCalls=' + netHls.startLoadCalls);
      A('COMP-005 a fatal NETWORK_ERROR does NOT misuse recoverMediaError()',
        !netHls.recoverCalls, 'recoverCalls=' + netHls.recoverCalls);

      render('media-node');
      await settle();
      const mediaHls = instances[1];
      mediaHls.trigger(FakeHls.Events.ERROR, { fatal: true, type: FakeHls.ErrorTypes.MEDIA_ERROR });
      await settle();
      A('COMP-005 a fatal MEDIA_ERROR still calls recoverMediaError() (regression guard)',
        (mediaHls.recoverCalls || 0) === 1, 'recoverCalls=' + mediaHls.recoverCalls);
      A('COMP-005 a fatal MEDIA_ERROR does not call startLoad()',
        !mediaHls.startLoadCalls, 'startLoadCalls=' + mediaHls.startLoadCalls);

      proto.play = origPlay;
      delete window.Hls;
    `,
  },
  {
    name: 'COMP-006 tweaks-panel slider/toggle/select controls have a programmatic label',
    target: 'tweaks-panel.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      ReactDOM.flushSync(() => root.render(React.createElement(window.TweakSlider, {
        label: '字體大小', value: 16, min: 10, max: 32, unit: 'px', onChange: () => {},
      })));
      const range = container.querySelector('input[type="range"]');
      A('COMP-006 TweakSlider input has an accessible name matching its visual label',
        !!range && range.getAttribute('aria-label') === '字體大小', range && range.getAttribute('aria-label'));

      ReactDOM.flushSync(() => root.render(React.createElement(window.TweakToggle, {
        label: '深色模式', value: true, onChange: () => {},
      })));
      const toggle = container.querySelector('button[role="switch"]');
      A('COMP-006 TweakToggle switch has an accessible name matching its visual label',
        !!toggle && toggle.getAttribute('aria-label') === '深色模式', toggle && toggle.getAttribute('aria-label'));

      ReactDOM.flushSync(() => root.render(React.createElement(window.TweakSelect, {
        label: '密度', value: 'regular', options: ['compact', 'regular', 'comfy'], onChange: () => {},
      })));
      const select = container.querySelector('select');
      A('COMP-006 TweakSelect has an accessible name matching its visual label',
        !!select && select.getAttribute('aria-label') === '密度', select && select.getAttribute('aria-label'));
    `,
  },
  {
    name: 'COMP-007 NodeSidePanel does not fake a successful save when onUpdateNode is missing',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const node = {
        id: 'PUMP-01', name: '測試泵浦', location: '3F · 西側', status: 'online',
        type: 'pump', heartbeat: 5, upload: 5, level: 40, cycles: 0,
      };
      // onUpdateNode deliberately OMITTED — the exact contract gap COMP-007
      // describes: the caller forgot to wire it (or it hasn't loaded yet).
      ReactDOM.flushSync(() => root.render(React.createElement(window.NodeSidePanel, {
        node, history: [], onClose: () => {}, onJumpAlert: () => {}, onNavigate: () => {},
        onSelectAlert: () => {}, openAlerts: [],
      })));
      await settle();
      const editBtn = byText('button', '編輯');
      A('COMP-007 setup: 編輯 button renders', !!editBtn);
      click(editBtn);
      await settle();

      const input = container.querySelector('#node-location-input');
      A('COMP-007 setup: location editor opens', !!input);
      setInput(input, '4F · 東側');
      await settle();

      const saveBtn = byText('button', '儲存');
      A('COMP-007 setup: 儲存 button renders', !!saveBtn);
      click(saveBtn);
      await settle(12);

      A('COMP-007 missing onUpdateNode does NOT silently exit edit mode (fake success)',
        !!container.querySelector('#node-location-input'), container.textContent);
      A('COMP-007 missing onUpdateNode surfaces an honest error instead of pretending success',
        container.textContent.indexOf('尚未就緒') !== -1, container.textContent);
    `,
  },
];
