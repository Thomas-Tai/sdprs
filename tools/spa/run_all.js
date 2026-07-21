// Runs every SPA check in order and summarises. Exit 0 only if all the
// blocking checks pass (the class gate is advisory and never blocks).
//
// Usage: node run_all.js
const { spawnSync } = require('child_process');
const path = require('path');

const CHECKS = [
  { name: 'vendor integrity', script: 'check_vendor.js', blocking: true },
  { name: 'scope invariant', script: 'scope_probe.js', blocking: true },
  { name: 'syntax',          script: 'check_spa_syntax.js', blocking: true },
  { name: 'undefined refs',  script: 'check_spa_refs.js', blocking: true },
  { name: 'render tests',    script: 'render_tests.js', blocking: true },
  { name: 'tailwind tokens', script: 'check_spa_classes.js', blocking: false },
];

const failures = [];
for (const c of CHECKS) {
  console.log('\n' + '='.repeat(64));
  console.log('### ' + c.name + (c.blocking ? '' : '  (advisory)'));
  console.log('='.repeat(64));
  const r = spawnSync(process.execPath, [path.join(__dirname, c.script)], { stdio: 'inherit' });
  if (r.status !== 0 && c.blocking) failures.push(c.name);
}

console.log('\n' + '='.repeat(64));
if (failures.length) {
  console.log('FAILED: ' + failures.join(', '));
  console.log('Gates are not a release criterion on their own — but a red gate is a hard stop.');
  process.exit(1);
}
console.log('All blocking SPA checks passed.');
console.log('Reminder: green gates prove compile + render, NOT requirement coverage.');
process.exit(0);
