// Rebuild the browser UI and the standalone SDK included in Python wheels.
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
for (const script of ['build', 'build:browser']) {
  const child = Bun.spawn(['bun', 'run', script], {
    cwd: fileURLToPath(new URL('../packages/sdk/', import.meta.url)),
    stdout: 'inherit', stderr: 'inherit',
  });
  if (await child.exited !== 0) throw new Error(`${script} failed`);
}
for (const name of ['index.html', 'style.css', 'app.js']) {
  await Bun.write(`${root}src/lighttime/static/${name}`, Bun.file(`${root}examples/browser/${name}`));
}
await Bun.write(`${root}src/lighttime/static/sdk.js`, Bun.file(`${root}packages/sdk/dist/index.js`));
console.log('Packaged web controller and /lighttime.js SDK.');
