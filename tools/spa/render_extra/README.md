# render_extra/ — parallel-authored SPA render suites

`render_tests.js` is one monolithic file with a single `SUITES` array. When many
agents fix findings in parallel (each in its own git worktree), all editing that
one array would collide on every merge. Instead, each owner drops its suites into
its **own file here** — disjoint files merge with zero conflicts.

`render_tests.js` globs `render_extra/*.js` (sorted), and appends their suites to
the run. `node tools/spa/render_tests.js` (and `run_all.js`) pick them up
automatically.

## Contract

Each file exports an array of suite descriptors:

```js
// tools/spa/render_extra/<owner>.js
module.exports = [
  {
    name: 'SHELL-001 invalid page falls back',   // shows in the run output
    target: 'app.jsx',                            // the file UNDER TEST (shares scope with body)
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'], // scripts loaded first, in order
    body: `
      // test-logic SOURCE. Backticks ARE allowed in this file (unlike the inline
      // TEST_ strings in render_tests.js). You have the same helpers the PRELUDE
      // exposes: A(name, cond, detail), tick(), settle(n), click(el),
      // container, root (ReactDOM.createRoot), byText(sel, txt), setInput(el, v),
      // plus React, ReactDOM, and everything the target/deps published to window.
      window.SDPRS_USER = 'op';
      ReactDOM.flushSync(() => root.render(React.createElement(SomeComponent, props)));
      await settle();
      A('SHELL-001 renders a fallback, not a blank area', container.textContent.indexOf('找不到') !== -1, container.textContent.slice(0, 120));
    `,
  },
];
```

The loader wraps every `body` in the standard `async` + `PRELUDE` + `try/catch`
shell, so an uncaught throw becomes a failing assertion (named `"<name> threw"`)
rather than crashing the whole run.

## Rules

- `deps` order matters: a file can only see what EARLIER scripts published to
  `window`. Canonical order is `icons.jsx`, `data.jsx`, `api.jsx`,
  `components.jsx`, then pages. Only the `target` shares scope with your `body`
  (that is how you reach a file's unexported internals).
- One file per owner. Name it after your area, e.g. `shell.js`, `data-api.js`,
  `components.js`, `status.js`, `monitor-pumps.js`, `alerts-weather.js`,
  `handover-audit.js`.
- Every finding you fix gets at least one assertion here that FAILS before your
  fix and PASSES after (RED → GREEN). Pure dead-code deletions that have no
  observable behavior are the only exception; note those in your report instead.
