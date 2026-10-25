import { resolve, sep } from 'node:path';
const base = resolve(import.meta.dir, 'dist');
const server = Bun.serve({ hostname: '0.0.0.0', port: Number(process.env.LIGHTTIME_QA_PORT ?? 18925),
  async fetch(request) {
    const name = decodeURIComponent(new URL(request.url).pathname);
    const path = resolve(base, name === '/' ? 'index.html' : `.${name}`);
    if (!path.startsWith(base + sep)) return new Response('Not found', { status: 404 });
    const file = Bun.file(path);
    return await file.exists() ? new Response(file) : new Response('Not found', { status: 404 });
  },
});
console.log(`Fixture-only Android UI preview: ${server.url}`);
