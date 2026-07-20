// SDPRS SPA undefined-reference gate.
//
// The syntax gate only catches PARSE errors. It cannot catch a call to a symbol
// that was never defined — that's a ReferenceError thrown at render time, which
// blanks the panel (or the page) with a green syntax check. A real near-miss:
// palette JSX once called nodeStatusTextCls/DotCls/Label, none of which
// existed. The syntax gate said OK.
//
// SCOPE MODEL (this is the part an earlier version of this gate got wrong):
// each <script type="text/babel"> runs in its OWN top-level scope, NOT a shared
// global lexical scope. See scope_probe.js, which proves it against the real
// vendored babel.min.js. Five SPA files each declare `const useState_p` at line
// 3 without colliding — impossible under a shared scope.
//
// The earlier version unioned every file's top-level bindings and accepted any
// reference found in that union. That model cannot see the very bug this gate
// exists to catch: a bare cross-file reference to a symbol that lives as a
// top-level const in ANOTHER file and was never published to window. Invisible
// at runtime => ReferenceError, while the gate reports OK.
//
// Correct per-file allowed set:
//     host/library globals
//   ∪ names published to window by ANY file  (window.X = … / Object.assign(window, {…}))
//   ∪ that file's OWN top-level bindings
//
// Usage: node check_spa_refs.js [spaDir]
const fs = require('fs');
const path = require('path');
const { SPA_DIR, SPA_FILES } = require('./spa_files');

const SPA = process.argv[2] || SPA_DIR;
const Babel = require(path.join(SPA, 'vendor', 'babel.min.js'));
const parser = Babel.packages.parser;
const traverse = Babel.packages.traverse.default || Babel.packages.traverse;

const KNOWN = new Set([
  // JS builtins
  'Object','Array','String','Number','Boolean','Math','JSON','Date','RegExp',
  'Map','Set','WeakMap','WeakSet','Promise','Symbol','Proxy','Reflect','BigInt',
  'Error','TypeError','RangeError','SyntaxError','Intl','globalThis',
  'parseInt','parseFloat','isNaN','isFinite','NaN','Infinity','undefined',
  'encodeURIComponent','decodeURIComponent','encodeURI','decodeURI','structuredClone',
  // DOM / BOM
  'window','document','console','navigator','location','history','screen',
  'localStorage','sessionStorage','fetch','Headers','Request','Response',
  'setTimeout','clearTimeout','setInterval','clearInterval','requestAnimationFrame',
  'cancelAnimationFrame','queueMicrotask','alert','confirm','prompt',
  'WebSocket','EventSource','AbortController','FormData','URL','URLSearchParams',
  'AbortSignal','DOMException','TextEncoder','TextDecoder','ReadableStream',
  'Blob','File','FileReader','Image','Audio','Notification','IntersectionObserver',
  'ResizeObserver','MutationObserver','CustomEvent','Event','KeyboardEvent',
  'MouseEvent','FocusEvent','DOMParser','XMLHttpRequest','performance','crypto','matchMedia',
  'getComputedStyle','scrollTo','open','close','btoa','atob','Element','Node',
  'HTMLElement','SVGElement','Text','Range','Selection','ClipboardItem',
  // Libraries loaded via <script> before the SPA files
  'React','ReactDOM','Babel','tailwind','process','module','require','exports',
]);

const parse = (src) => parser.parse(src, {
  sourceType: 'script',
  errorRecovery: true,
  plugins: ['jsx', 'classProperties', 'optionalChaining', 'nullishCoalescingOperator'],
});

const ownBindings = new Map();   // file -> Set(top-level names)
const freeRefs = new Map();      // file -> Map(name -> [lines])
const publishedBy = new Map();   // name -> first file that publishes it
let parseFailed = 0;

for (const f of SPA_FILES) {
  const p = path.join(SPA, f);
  if (!fs.existsSync(p)) { console.log(`SKIP  ${f} (missing)`); continue; }
  let ast;
  try { ast = parse(fs.readFileSync(p, 'utf8')); }
  catch (e) { parseFailed++; console.log(`PARSE-FAIL  ${f}: ${e.message.split('\n')[0]}`); continue; }

  traverse(ast, {
    Program(pth) {
      ownBindings.set(f, new Set(Object.keys(pth.scope.bindings)));
      const g = new Map();
      for (const [name, nodes] of Object.entries(pth.scope.globals)) {
        const arr = Array.isArray(nodes) ? nodes : [nodes];
        g.set(name, arr.map(n => (n.loc && n.loc.start.line) || '?'));
      }
      freeRefs.set(f, g);
    },
    // window.Foo = …
    AssignmentExpression(pth) {
      const l = pth.node.left;
      if (l.type === 'MemberExpression' && !l.computed &&
          l.object.type === 'Identifier' && l.object.name === 'window' &&
          l.property.type === 'Identifier' && !publishedBy.has(l.property.name)) {
        publishedBy.set(l.property.name, f);
      }
    },
    // Object.assign(window, { Foo, Bar: baz })
    CallExpression(pth) {
      const c = pth.node.callee;
      const isObjAssign = c.type === 'MemberExpression' && !c.computed &&
        c.object.type === 'Identifier' && c.object.name === 'Object' &&
        c.property.type === 'Identifier' && c.property.name === 'assign';
      if (!isObjAssign) return;
      const [target, ...rest] = pth.node.arguments;
      if (!target || target.type !== 'Identifier' || target.name !== 'window') return;
      for (const arg of rest) {
        if (!arg || arg.type !== 'ObjectExpression') continue;
        for (const prop of arg.properties) {
          const key = prop.key;
          if (key && key.type === 'Identifier' && !publishedBy.has(key.name)) {
            publishedBy.set(key.name, f);
          }
        }
      }
    },
  });
}

let hard = 0, ordering = 0;
const fileIndex = new Map(SPA_FILES.map((f, i) => [f, i]));

for (const f of SPA_FILES) {
  const g = freeRefs.get(f);
  if (!g) continue;
  const own = ownBindings.get(f) || new Set();
  const bad = [], late = [];
  for (const [name, lines] of g) {
    if (KNOWN.has(name) || own.has(name)) continue;
    if (publishedBy.has(name)) {
      // Published — but by a file loaded LATER? Only a problem if touched at
      // script-eval time; inside a component body it resolves by render time.
      const src = publishedBy.get(name);
      if (fileIndex.get(src) > fileIndex.get(f)) {
        late.push(`${name} (line ${lines.slice(0, 3).join(', ')}) published later by ${src}`);
      }
      continue;
    }
    bad.push(`${name} (line ${lines.slice(0, 4).join(', ')})`);
  }
  if (bad.length) {
    hard += bad.length;
    console.log(`UNDEFINED  ${f}`);
    for (const b of bad) console.log(`           ${b}`);
  } else {
    console.log(`OK    ${f}`);
  }
  for (const l of late) { ordering++; console.log(`  LOAD-ORDER  ${f}: ${l}`); }
}

console.log('\n' + (hard
  ? `${hard} reference(s) UNRESOLVABLE under the real (isolated) scope model — ReferenceError at runtime.`
  : 'No unresolvable references under the isolated scope model.'));
if (ordering) console.log(`${ordering} load-order note(s) above (fine if only touched after mount).`);

process.exit(hard || parseFailed ? 1 : 0);
