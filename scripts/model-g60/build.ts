import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(import.meta.dir, '../../artifacts/redesign-20261025/model');
await mkdir(output, { recursive: true });
const result = await Bun.build({
  entrypoints: [resolve(import.meta.dir, 'studio.ts')],
  target: 'browser',
  minify: true,
});
if (!result.success) throw new Error(result.logs.join('\n'));
const bundle = await result.outputs[0].text();
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lighttime · G60 procedural studio</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#16090c;color:#f3eae0;font:14px system-ui,sans-serif}
main{max-width:1100px;margin:auto;padding:24px}header{display:flex;align-items:center;justify-content:space-between;gap:20px}
h1{font-size:18px;font-weight:500;letter-spacing:-.02em}p{color:#b9a2a2;max-width:600px;line-height:1.6}
nav{display:flex;gap:8px;flex-wrap:wrap}button,a{background:#3f1721;color:#f5eae2;border:1px solid #75404b;border-radius:24px;padding:10px 16px;font:inherit;text-decoration:none;cursor:pointer}
#stage{margin-top:18px;border-radius:24px;background:radial-gradient(ellipse at 70% 35%,#5e1528 0%,#240d15 42%,#10090c 75%);overflow:hidden}
canvas{display:block;width:100%;height:auto}#status{font:12px ui-monospace,monospace;color:#cfaeae}footer{display:flex;justify-content:space-between;gap:20px;padding-top:18px;font-size:12px}
</style></head><body><main><header><h1>MOLUS G60 / procedural study</h1><nav><button id="front">Studio view</button><button id="rear">Controls</button><button id="export">Rebuild assets</button><a href="/assets/g60.glb" download>Download GLB</a></nav></header>
<div id="stage"></div><footer><span>Original approximation · warm softboxes / ruby edge</span><span id="status">Building model…</span></footer>
<p>Procedural geometry, materials and textures. The hero PNG has a transparent background; the ruby surface shown here is only the viewing backdrop. The rear display is illustrative.</p>
</main><script type="module">${bundle.replaceAll('</script', '<\\/script')}</script></body></html>`;
await Bun.write(resolve(output, 'studio.html'), html);
console.log(`Built ${output}/studio.html (${Math.round(html.length / 1024)} KiB, no remote dependencies)`);
