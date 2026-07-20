# SPA checks

Offline static gates and jsdom render tests for the dashboard SPA
(`central_server/static/spa/`).

**The SPA still has NO build step.** This directory is developer tooling only —
nothing here runs in production, nothing here is served to a browser, and the
`.jsx` files are still compiled in-browser by the vendored Babel at page load.
Do not turn this into a bundler step without an explicit decision.

## Setup

```bash
cd tools/spa
npm install        # jsdom only; node_modules/ is gitignored
```

React, ReactDOM, Babel and Tailwind all come from `static/spa/vendor/`, so the
checks run with no network access.

## Run

```bash
npm run check      # everything, with a summary  (this is the one to run)

npm run syntax     # every .jsx compiles under the vendored Babel
npm run refs       # no undefined references (isolated-scope model)
npm run render     # jsdom render tests
npm run classes    # advisory Tailwind token check
npm run scope      # re-verify the script-scope invariant
```

`npm run check` exits non-zero if any blocking check fails. The Tailwind token
check is advisory and never blocks — false positives are expected.

## What each check catches

Three failure modes are invisible to the eye in a no-build SPA, and each has
actually shipped in this project:

| Check | Failure mode | Symptom without the gate |
|---|---|---|
| `syntax` | parse error | **entire dashboard blank**, only a console message |
| `refs` | undefined symbol | `ReferenceError` at render — **panel blanks** |
| `classes` | class not in `tailwind.config` | element renders with **no styling at all**, silently |
| `render` | compiles and reads fine, but behaves wrong | wrong command sent, banner never shown |

`syntax` and `refs` prove a file *compiles* and *reads* correctly. They cannot
prove it *renders and behaves* correctly — that is what `render_tests.js` is
for. It mounts real components, dispatches real clicks, and asserts the real
DOM, with `window.SDPRS_API` mocked by spies.

## Script scope: isolated per file

Each `<script type="text/babel">` runs in its **own top-level scope**, not a
shared global lexical scope. `scope_probe.js` proves this against the real
vendored `babel.min.js`; the corroborating evidence in the source is that
`pages/alerts|audit|handover|monitor|status.jsx` each declare
`const useState_p` at line 3 without colliding.

Consequences, and why they matter here:

- A top-level `const`/`function` in one file is **invisible** to another. Bare
  cross-file identifiers resolve only because the symbol was published to
  `window` (`window.X = …` / `Object.assign(window, {…})`), which is why every
  page ends with `Object.assign(window, { …Page })` and `app.jsx` renders
  `<window.StatusPage/>`.
- `check_spa_refs.js` must therefore use **per-file** allowed sets. An earlier
  version unioned every file's top-level bindings, which structurally could not
  see a cross-file reference that was never published — it would report OK on a
  runtime `ReferenceError`.
- `render_tests.js` loads each dependency as its **own** script and shares a
  scope only between the file under test and the test code. Concatenating all
  files is over-permissive and can green-light a reference the browser throws on.

If `npm run scope` ever fails, a Babel upgrade changed this invariant: revisit
both `check_spa_refs.js` and `render_tests.js`, and expect the five
`const useState_p` declarations to start colliding.

## Render test coverage

36 assertions. Deliberately weighted toward operator-safety and state-machine
logic rather than breadth.

| Finding | What is executed, not merely read |
|---|---|
| MSP-F6 *(safety)* | `手動停機中` / `手動強制運行中` banners render on manual override OFF/ON; **clicking 恢復自動 actually calls `pumpCommand(id,'AUTO')`**; absent when there is no override |
| MSP-F5 | `上次指令` line renders operator and time from `lastPumpCommand` |
| MSP-F7 | stale cached `bitrate>0` with `health.reachable=false` still shows 開始串流 and clicking calls `startStream` — health, not cached bitrate, decides the command; two clicks in one tick fire exactly one command; missing API renders disabled |
| API-F9 | `短循環警告` banner renders iff `node.cyclesAlert` (the clean mapped boolean, not `n._cycles.alert`) |
| CMP-F11 | picking a node in the palette calls `onNav('status')` **and** `onCmd('node:<id>')`, then closes |
| WHA-M8 | a 409 opens the 儲存衝突 dialog showing **both** the server text and the operator's draft; 覆蓋伺服器版本 re-issues with the server's new token from the 409 body |
| WHA-L8 | number input tolerates an empty field — no snap-to-0, no `onChange(0)` mid-typing |

**Not covered: pixels.** Tailwind is not applied in jsdom, so these tests check
component logic and DOM structure, never layout or color.

**jsdom quirk:** a post-blur `setState` is not deterministically flushed to the
DOM. Assert blur handlers through an observable callback branch (see WHA-L8),
not through DOM reflection.

## Adding a test

Add a suite to the `SUITES` array in `render_tests.js`:

```js
{ name: 'FINDING-ID  file.jsx',
  deps: ['icons.jsx', 'data.jsx'],   // loaded as separate scripts
  target: 'pages/thing.jsx',         // shares scope with your test code
  test: TEST_THING }
```

Put shared dependencies in `deps` and the file you are testing in `target` —
that is what lets the test reach internal, unpublished components.

**No backticks inside the test-code template strings.** A backtick there closes
the enclosing template literal early and produces a baffling syntax error. Use
string concatenation.
