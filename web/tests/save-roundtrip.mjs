/* npm-test wrapper around save_roundtrip.py. Skips cleanly (exit 0) if python3
 * is not installed, so minimal CI never breaks. */
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const py = join(HERE, 'save_roundtrip.py');

for (const bin of ['python3', 'python']) {
  const probe = spawnSync(bin, ['--version'], { stdio: 'ignore' });
  if (probe.status === 0) {
    const r = spawnSync(bin, [py], { stdio: 'inherit' });
    process.exit(r.status || 0);
  }
}
console.log('save_roundtrip: python3 not found — skipping (exit 0)');
process.exit(0);
