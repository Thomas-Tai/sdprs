// SDPRS SPA syntax gate.
//
// The dashboard has NO build step — Babel compiles each .jsx in the browser at
// load time, so a single syntax error blanks the whole page with only a console
// message. This runs the SAME vendored Babel over every SPA source file.
//
// Usage: node check_spa_syntax.js [spaDir]
const fs = require('fs');
const path = require('path');
const { SPA_DIR, SPA_FILES } = require('./spa_files');

const SPA = process.argv[2] || SPA_DIR;
const Babel = require(path.join(SPA, 'vendor', 'babel.min.js'));

let failed = 0;
for (const f of SPA_FILES) {
  const p = path.join(SPA, f);
  if (!fs.existsSync(p)) { console.log(`SKIP  ${f} (missing)`); continue; }
  try {
    Babel.transform(fs.readFileSync(p, 'utf8'), { presets: ['env', 'react'], filename: f });
    console.log(`OK    ${f}`);
  } catch (e) {
    failed++;
    console.log(`FAIL  ${f}\n      ${e.message.split('\n')[0]}`);
  }
}
console.log(failed ? `\n${failed} file(s) FAILED to compile` : '\nAll SPA files compile.');
process.exit(failed ? 1 : 0);
