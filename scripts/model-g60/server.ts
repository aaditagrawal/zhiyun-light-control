import { resolve } from 'node:path';

const root = resolve(import.meta.dir, '../..');
const artifact = resolve(root, 'artifacts/redesign-20261025/model');
const assets = resolve(root, 'apps/android/assets/g60');
const outputs: Record<string, string> = {
  'hero.png': resolve(assets, 'hero.png'),
  'compact.png': resolve(assets, 'compact.png'),
  'glow.png': resolve(assets, 'glow.png'),
  'g60.glb': resolve(assets, 'g60.glb'),
  'rear.png': resolve(artifact, 'rear.png'),
  'render-metrics.json': resolve(artifact, 'render-metrics.json'),
  // Optional `?preset=ruby` render; separate files beside the default images.
  'hero-ruby.png': resolve(assets, 'hero-ruby.png'),
  'compact-ruby.png': resolve(assets, 'compact-ruby.png'),
  'ruby-render-metrics.json': resolve(artifact, 'ruby-render-metrics.json'),
};
const port = Number(process.env.G60_STUDIO_PORT ?? 9036);
const server = Bun.serve({
  hostname: process.env.G60_STUDIO_HOST ?? '0.0.0.0', port,
  async fetch(req) {
    const url = new URL(req.url);
    if (req.method === 'POST' && url.pathname.startsWith('/capture/')) {
      const name = decodeURIComponent(url.pathname.slice('/capture/'.length));
      const target = outputs[name];
      if (!target) return new Response('Unknown artifact', { status: 400 });
      const bytes = await req.arrayBuffer();
      if (bytes.byteLength > 20_000_000) return new Response('Too large', { status: 413 });
      await Bun.write(target, bytes);
      console.log(`Saved ${name}: ${bytes.byteLength} bytes`);
      return Response.json({ saved: name, bytes: bytes.byteLength });
    }
    if (url.pathname === '/') return new Response(Bun.file(resolve(artifact, 'studio.html')));
    if (url.pathname.startsWith('/assets/')) {
      const name = url.pathname.slice('/assets/'.length);
      if (!Object.keys(outputs).includes(name)) return new Response('Not found', { status: 404 });
      return new Response(Bun.file(outputs[name]));
    }
    return new Response('Not found', { status: 404 });
  },
});
console.log(`G60 studio: http://${server.hostname}:${server.port} (Ctrl-C to stop)`);
