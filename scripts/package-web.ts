import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
const destination = join(root, 'src/lighttime/static');
await mkdir(destination, { recursive: true });
for (const name of ['index.html', 'style.css', 'app.js']) {
  const source = Bun.file(join(root, 'examples/browser', name));
  if (!(await source.exists())) throw new Error(`Build browser assets first: missing ${name}`);
  await Bun.write(join(destination, name), source);
}
console.log('Browser assets copied into the Python package.');
