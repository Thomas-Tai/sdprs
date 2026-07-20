// SDPRS SPA Tailwind-token gate.
//
// Third runtime-invisible failure mode, after parse errors and undefined refs:
// a utility class that isn't in tailwind.config renders NOTHING. No error
// anywhere. Real cases already shipped: `animate-spin-slow` (dead boot spinner)
// and `bg-brand-primary` (the only button on a blocking modal, invisible).
//
// Strategy: don't try to validate all of Tailwind. Only check the COLOR and
// ANIMATION namespaces, where this project defines custom tokens — flag any
// color family that is neither a Tailwind built-in palette nor one of ours.
//
// ADVISORY ONLY — always exits 0. False positives are expected (the Tailwind
// Play CDN ships the full default palette, so slate/red/sky hits are fine).
//
// Usage: node check_spa_classes.js [spaDir]
const fs = require('fs');
const path = require('path');
const { SPA_DIR } = require('./spa_files');

const SPA = process.argv[2] || SPA_DIR;

// Families defined in index.html's tailwind.config theme.extend.colors.
const CUSTOM_FAMILIES = new Set(['sev', 'surface', 'ink', 'border']);
// Tailwind 3 built-in palettes + keywords.
const BUILTIN_FAMILIES = new Set([
  'slate','gray','zinc','neutral','stone','red','orange','amber','yellow','lime',
  'green','emerald','teal','cyan','sky','blue','indigo','violet','purple',
  'fuchsia','pink','rose','black','white','transparent','current','inherit','none','auto',
]);
// Animations: config theme.extend.animation + Tailwind built-ins.
const KNOWN_ANIM = new Set([
  'pulse-critical','live-blink','spin-slow',   // ours
  'spin','ping','pulse','bounce','none',       // built-in
]);

const COLOR_PREFIXES = [
  'bg','text','border','ring','fill','stroke','divide','outline',
  'decoration','accent','caret','shadow','from','via','to','placeholder',
];

const files = fs.readdirSync(SPA).filter(f => f.endsWith('.jsx'))
  .concat(fs.existsSync(path.join(SPA, 'pages'))
    ? fs.readdirSync(path.join(SPA, 'pages')).filter(f => f.endsWith('.jsx')).map(f => 'pages/' + f)
    : [])
  .concat(['index.html']);

const suspect = new Map();   // class -> Map(file -> count)
const seenCustom = new Set();

function note(cls, file) {
  if (!suspect.has(cls)) suspect.set(cls, new Map());
  const m = suspect.get(cls);
  m.set(file, (m.get(file) || 0) + 1);
}

for (const f of files) {
  const p = path.join(SPA, f);
  if (!fs.existsSync(p)) continue;
  const src = fs.readFileSync(p, 'utf8');

  // Deliberately broad: template literals and conditional expressions mean we
  // can't rely on parsing className attributes cleanly.
  const tokens = src.match(/[a-z][a-z0-9]*(?:-[a-z0-9./[\]%]+)+/gi) || [];

  for (const raw of tokens) {
    // strip responsive/state variants: md:, hover:, dark:, group-hover: …
    const cls = raw.includes(':') ? raw.slice(raw.lastIndexOf(':') + 1) : raw;
    const bare = cls.replace(/^[!-]/, '').split('/')[0];   // drop opacity suffix

    const anim = bare.match(/^animate-(.+)$/);
    if (anim) {
      if (!KNOWN_ANIM.has(anim[1])) note(bare, f);
      continue;
    }

    const parts = bare.split('-');
    if (parts.length < 2) continue;
    if (!COLOR_PREFIXES.includes(parts[0])) continue;
    const family = parts[1];

    // numeric scale (text-2xl, border-2) or arbitrary value — not a color
    if (/^\[/.test(family) || /^\d/.test(family)) continue;
    if (BUILTIN_FAMILIES.has(family)) continue;
    if (CUSTOM_FAMILIES.has(family)) { seenCustom.add(bare); continue; }

    // Not a known family. Could be a non-color utility sharing a prefix
    // (text-center, border-solid, shadow-lg…) — filter the common ones.
    if (/^(center|left|right|justify|start|end|top|bottom|solid|dashed|dotted|double|hidden|none|inset|xs|sm|md|lg|xl|full|screen|px|auto|wrap|nowrap|balance|pretty|clip|ellipsis|opacity|offset|collapse|separate|current|transparent)$/.test(family)) continue;

    note(bare, f);
  }
}

console.log('--- custom tokens in use (sanity check, these are valid) ---');
console.log([...seenCustom].sort().join('  ') || '(none)');

if (suspect.size) {
  console.log('\n--- SUSPECT: not a known color family or animation ---');
  for (const [cls, m] of [...suspect].sort()) {
    const where = [...m].map(([f, n]) => `${f}×${n}`).join(', ');
    console.log(`  ${cls}\n      ${where}`);
  }
  console.log(`\n${suspect.size} suspect class(es). Each renders as NOTHING if genuinely undefined.`);
  console.log('Review by hand — this checker is advisory, false positives are expected.');
} else {
  console.log('\nNo suspect color/animation classes.');
}
