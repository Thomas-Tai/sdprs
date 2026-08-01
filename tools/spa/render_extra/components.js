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
  {
    name: 'COMP-008 StatusStrip weather chip survives a partial WEATHER payload (missing .wind)',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // A schema-drift / partial weather payload — available + rain present,
      // wind entirely absent. StatusStrip's weather chip must degrade, not
      // take the whole status strip down with it.
      window.WEATHER = { available: true, typhoon: null, wind: undefined,
        rain: { now: 5, hour: null, day: null }, lightning: null };
      const noop = () => {};
      const props = { unackCount: 0, muted: false, setMuted: noop, theme: 'dark', setTheme: noop,
        onOpenShortcuts: noop, page: 'monitor', setPage: noop, onOpenMuteDrawer: noop, audioReplayIn: 0,
        muteState: { nodes: [], sources: [], global: false }, operators: [], staleAckCount: 0,
        onOpenCmdK: noop, focusMode: false, onToggleFocus: noop };
      let threw = false;
      try {
        ReactDOM.flushSync(() => root.render(React.createElement(window.StatusStrip, props)));
        await settle();
      } catch (e) { threw = true; }
      A('COMP-008 StatusStrip renders without crashing when WEATHER.wind is missing', threw === false);
      A('COMP-008 the rest of the status strip still renders (unack pill area present)',
        container.textContent.indexOf('SDPRS') !== -1);
    `,
  },
  {
    name: 'COMP-009 CommandPalette clamps the highlight index when live results shrink',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // The palette stays open while app.jsx's ~20s poll keeps handing it
      // fresh alerts/nodes props — the operator need not type anything for
      // the underlying item list to shrink (an alert got resolved elsewhere,
      // a node was removed). hi must never point past the end of the new,
      // shorter matches array.
      window.NAV_ITEMS = [];
      // ZQ- prefix collides with none of the palette's hardcoded nav/cmd
      // item strings, so the query below matches ONLY these 3 alerts —
      // deterministic, unlike a bare 'A' (which also matches e.g. the
      // hardcoded 'audit-me' command id).
      let alerts = [
        { id: 'ZQ1', type: 'glass_break', node: 'CAM-01', state: 'pending', sev: 'critical' },
        { id: 'ZQ2', type: 'glass_break', node: 'CAM-02', state: 'pending', sev: 'warn' },
        { id: 'ZQ3', type: 'glass_break', node: 'CAM-03', state: 'pending', sev: 'info' },
      ];
      const render = () => ReactDOM.flushSync(() => root.render(React.createElement(window.CommandPalette, {
        open: true, onClose: () => {}, alerts, nodes: [],
        onSelectAlert: () => {}, onNav: () => {}, onCmd: () => {},
      })));
      render();
      await settle();

      const input = container.querySelector('input[role="combobox"]');
      setInput(input, 'ZQ');
      await settle();
      let options = Array.from(container.querySelectorAll('button[role="option"]'));
      A('COMP-009 setup: query matches all 3 alerts', options.length === 3, options.length);

      // Move the highlight down to the LAST row (index 2).
      input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
      input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
      await settle();
      options = Array.from(container.querySelectorAll('button[role="option"]'));
      A('COMP-009 setup: highlight sits on the last (3rd) row', options[2].getAttribute('aria-selected') === 'true');

      // Live data shrinks the list to 2 items WITHOUT the operator typing —
      // the same query ('A') still matches both remaining alerts.
      alerts = alerts.slice(0, 2);
      render();
      await settle();
      options = Array.from(container.querySelectorAll('button[role="option"]'));
      A('COMP-009 setup: the list is now only 2 rows', options.length === 2, options.length);
      const anySelected = options.some(o => o.getAttribute('aria-selected') === 'true');
      A('COMP-009 the stale highlight index does not orphan (some row is still highlighted)', anySelected);
      const activeDescendant = input.getAttribute('aria-activedescendant');
      A('COMP-009 aria-activedescendant points at a row that still exists',
        !!activeDescendant && !!container.querySelector('#' + activeDescendant), activeDescendant);
    `,
  },
  {
    name: 'COMP-010 Sparkline sanitizes non-numeric buckets (no NaNpx bar heights)',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // A malformed/partial rate payload (schema drift, a bucket that failed
      // to compute upstream) can hand this component null/undefined/NaN/a
      // stray string instead of a count. Math.max(...data) is poisoned by a
      // single NaN, so one bad bucket used to blank the ENTIRE sparkline, not
      // just its own bar.
      ReactDOM.flushSync(() => root.render(React.createElement(window.Sparkline, {
        data: [3, null, 5, undefined, NaN, 'x', 2],
      })));
      await settle();
      const bars = Array.from(container.querySelectorAll('.rounded-sm'));
      A('COMP-010 setup: one bar rendered per bucket', bars.length === 7, bars.length);
      const heights = bars.map(b => b.style.height);
      // A "NaNpx" height string is an INVALID CSS length — jsdom (and real
      // browsers) silently refuse to apply it, so the bar's style.height
      // reads back as '' rather than the literal text "NaNpx". The
      // observable symptom is therefore an EMPTY/collapsed bar, not a
      // string containing "NaN" — assert every bar gets a real, finite,
      // parseable pixel height instead.
      A('COMP-010 every bar gets a valid finite pixel height (none collapse to an invalid NaNpx length)',
        heights.every(h => /^\\d+(\\.\\d+)?px$/.test(h)), heights.join(','));
    `,
  },
  {
    name: 'COMP-012 StatusStrip mute-drawer trigger uses aria-haspopup, not aria-pressed',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // This button OPENS a drawer overlay (a disclosure interaction) — it
      // does not itself toggle a persistent state the way the theme/focus
      // buttons do. aria-pressed tells assistive tech "activating ME flips
      // MY own boolean state", which is the wrong contract for a trigger
      // whose activation opens a whole separate dialog.
      const noop = () => {};
      const props = { unackCount: 0, muted: false, theme: 'dark', setTheme: noop,
        onOpenShortcuts: noop, setPage: noop, onOpenMuteDrawer: noop, audioReplayIn: 0,
        muteState: { nodes: ['CAM-1'], sources: [], global: false }, operators: [], staleAckCount: 0,
        onOpenCmdK: noop, focusMode: false, onToggleFocus: noop };
      ReactDOM.flushSync(() => root.render(React.createElement(window.StatusStrip, props)));
      await settle();
      const trigger = Array.from(container.querySelectorAll('button')).find(b => (b.title || '').indexOf('音效 (M)') === 0);
      A('COMP-012 setup: the mute-drawer trigger button renders', !!trigger);
      A('COMP-012 the trigger advertises a popup (aria-haspopup), not a self-toggle (aria-pressed)',
        !!trigger && trigger.getAttribute('aria-haspopup') === 'dialog' && trigger.getAttribute('aria-pressed') === null,
        trigger && ('aria-haspopup=' + trigger.getAttribute('aria-haspopup') + ' aria-pressed=' + trigger.getAttribute('aria-pressed')));
    `,
  },
  {
    name: 'COMP-014 Sparkline memo compares data by content, not just reference',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // React.memo(Component, compare) stores the comparator on the returned
      // object's own .compare property (plain object, not a hidden internal) —
      // reachable here without mounting anything, which lets this test prove
      // the COMPARATOR's behavior directly rather than inferring it indirectly
      // from render-count side effects.
      const compare = window.Sparkline && window.Sparkline.compare;
      A('COMP-014 setup: Sparkline is a React.memo with a custom comparator', typeof compare === 'function');

      // Every ~20s poll (and every WS event) hands this component a FRESH
      // array computed fresh from the current alert list — reference equality
      // is therefore false on every single render even when the 16 bucket
      // VALUES have not changed, so the memo never once bailed out.
      const same = compare(
        { data: [1, 2, 3], width: 240, height: 28 },
        { data: [1, 2, 3], width: 240, height: 28 },
      );
      A('COMP-014 two different array REFERENCES with identical VALUES are treated as equal (memo actually bails)',
        same === true, same);

      const diffValue = compare(
        { data: [1, 2, 3], width: 240, height: 28 },
        { data: [1, 2, 9], width: 240, height: 28 },
      );
      A('COMP-014 an actual bucket-value change is still detected as unequal', diffValue === false, diffValue);

      const diffLength = compare(
        { data: [1, 2, 3], width: 240, height: 28 },
        { data: [1, 2, 3, 4], width: 240, height: 28 },
      );
      A('COMP-014 a length change is still detected as unequal', diffLength === false, diffLength);

      const diffHeight = compare(
        { data: [1, 2, 3], width: 240, height: 28 },
        { data: [1, 2, 3], width: 240, height: 40 },
      );
      A('COMP-014 a changed height prop is still detected as unequal (regression guard)', diffHeight === false, diffHeight);
    `,
  },
  {
    name: 'COMP-015 AudioController overlap-guard does not advance its timestamp while muted',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // Minimal AudioContext double so beep() takes its real code path
      // instead of short-circuiting on "no AudioContext in jsdom". Counts
      // oscillators created — playCritical() creates exactly 3 per firing,
      // playWarning() exactly 2 — so a count proves whether a beep actually
      // happened, without needing real sound output.
      function FakeGain() { return { gain: { value: 0, setValueAtTime(){}, linearRampToValueAtTime(){} }, connect(){} }; }
      function FakeOsc() { return { type: '', frequency: { value: 0 }, connect(){}, start(){}, stop(){} }; }
      function FakeAudioContext() {
        this.currentTime = 0;
        this.destination = {};
      }
      FakeAudioContext.prototype.createGain = function () { return FakeGain(); };
      FakeAudioContext.prototype.createOscillator = function () { window.__oscCount = (window.__oscCount || 0) + 1; return FakeOsc(); };
      FakeAudioContext.prototype.resume = function () { return Promise.resolve(); };
      window.AudioContext = FakeAudioContext;
      window.__oscCount = 0;

      const ctrl = window.SDPRS_AUDIO;
      A('COMP-015 setup: AudioController is published', !!ctrl);

      // Alerts keep arriving while the operator has muted audio — none of
      // these should produce sound (muted) AND none should count toward the
      // overlap guard (they never actually played).
      ctrl.setMuted(true);
      ctrl.playCritical();
      A('COMP-015 setup: no oscillator is created while muted', window.__oscCount === 0, window.__oscCount);

      // The operator unmutes and a genuine new critical alert fires
      // immediately after. This must actually beep — the overlap guard
      // exists to stop DOUBLE beeps for the SAME real alert burst, not to
      // swallow the first real beep after a mute period.
      ctrl.setMuted(false);
      ctrl.playCritical();
      A('COMP-015 the first unmuted playCritical right after a muted call actually beeps',
        window.__oscCount === 3, window.__oscCount);

      delete window.AudioContext;
    `,
  },
];
