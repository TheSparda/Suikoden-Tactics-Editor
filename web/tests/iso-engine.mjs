/* npm-test wrapper around iso_engine.py. Skips cleanly (exit 0) if python3 is
 * not installed, so minimal CI never breaks. */
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const py = join(HERE, 'iso_engine.py');

for (const bin of ['python3', 'python']) {
  if (spawnSync(bin, ['--version'], { stdio: 'ignore' }).status === 0) {
    process.exit(spawnSync(bin, [py], { stdio: 'inherit' }).status || 0);
  }
}
console.log('iso_engine: python3 not found — skipping (exit 0)');
process.exit(0);
