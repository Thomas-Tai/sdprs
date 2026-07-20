// Executable proof of the SPA's script-scope invariant.
//
// check_spa_refs.js and render_tests.js both depend on this fact: each
// <script type="text/babel"> runs in its OWN top-level scope, NOT a shared
// global lexical scope. If a future Babel upgrade changed that, five SPA files
// would collide on `const useState_p` and the whole dashboard would go blank —
// so this is worth being able to re-check on demand.
//
// Method: real vendored babel.min.js, real jsdom, two text/babel blocks each
// declaring the same top-level const.
//
// Usage: node scope_probe.js [spaDir]
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const { SPA_DIR } = require('./spa_files');

const SPA = process.argv[2] || SPA_DIR;
const babelSrc = fs.readFileSync(path.join(SPA, 'vendor', 'babel.min.js'), 'utf8');

const errors = [];
const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body>
<script type="text/babel">const useState_p = 1; window.__A = useState_p;</script>
<script type="text/babel">const useState_p = 2; window.__B = useState_p;</script>
</body></html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

dom.window.addEventListener('error', e => errors.push(String(e.error || e.message)));

const s = dom.window.document.createElement('script');
s.textContent = babelSrc;
dom.window.document.head.appendChild(s);

try { dom.window.Babel.transformScriptTags(); }
catch (e) { errors.push('transformScriptTags threw: ' + e.message); }

const isolated = dom.window.__A === 1 && dom.window.__B === 2 && errors.length === 0;

console.log('first script const  :', dom.window.__A);
console.log('second script const :', dom.window.__B);
console.log('errors              :', errors.length ? errors : '(none)');
console.log('\n' + (isolated
  ? 'OK — scopes are ISOLATED. Cross-file symbols must be published to window.\n' +
    '     check_spa_refs.js models this correctly.'
  : 'CHANGED — scopes are no longer isolated (or Babel failed).\n' +
    '     check_spa_refs.js and render_tests.js assume isolation; revisit both,\n' +
    '     and check the 5 files that each declare `const useState_p` at line 3.'));

process.exit(isolated ? 0 : 1);
