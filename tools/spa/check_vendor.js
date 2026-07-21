// SDPRS SPA vendor-integrity gate.
//
// Global Constraint #1 (disaster resilience) requires every vendor script to
// be loaded from disk — zero CDN requests at runtime. That guarantee is only
// as good as "the bytes committed are the bytes intended": a size check
// ("is it >100KB") cannot tell a real hls.js build apart from a corrupted
// download or an HTML error page saved without `curl -f`. This gate parses
// vendor/VENDOR.md's provenance table and re-hashes every listed file,
// failing on any digest mismatch or missing file.
//
// Dependency-free by design (fs + crypto only), matching the sibling gates.
//
// Usage: node check_vendor.js [spaDir]
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { SPA_DIR } = require('./spa_files');

const SPA = process.argv[2] || SPA_DIR;
const VENDOR_DIR = path.join(SPA, 'vendor');
const MANIFEST = path.join(VENDOR_DIR, 'VENDOR.md');

if (!fs.existsSync(MANIFEST)) {
  console.log(`FAIL  manifest not found: ${MANIFEST}`);
  process.exit(1);
}

// Table rows look like: | `hls.min.js` | 1.5.13 | https://... | `<sha256>` |
// Only the first (file) and last (digest) backtick-quoted cells matter here.
const ROW_RE = /^\|\s*`([^`]+)`\s*\|.*\|\s*`([0-9a-fA-F]{64})`\s*\|\s*$/;

const entries = [];
const lines = fs.readFileSync(MANIFEST, 'utf8').split(/\r?\n/);
for (const line of lines) {
  const m = ROW_RE.exec(line);
  if (m) entries.push({ file: m[1], sha256: m[2].toLowerCase() });
}

if (!entries.length) {
  console.log(`FAIL  no vendor entries parsed from ${MANIFEST}`);
  process.exit(1);
}

let failed = 0;
for (const { file, sha256 } of entries) {
  const p = path.join(VENDOR_DIR, file);
  if (!fs.existsSync(p)) {
    failed++;
    console.log(`FAIL  ${file}  (missing file)`);
    continue;
  }
  const actual = crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
  if (actual === sha256) {
    console.log(`OK    ${file}`);
  } else {
    failed++;
    console.log(`FAIL  ${file}`);
    console.log(`      expected ${sha256}`);
    console.log(`      actual   ${actual}`);
  }
}

console.log(failed
  ? `\n${failed} vendor file(s) FAILED integrity check.`
  : `\nAll ${entries.length} vendor file(s) match their pinned SHA-256.`);
process.exit(failed ? 1 : 0);
