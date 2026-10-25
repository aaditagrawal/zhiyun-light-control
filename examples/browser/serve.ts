/** Static QA/example server; the Python bridge can serve these same three assets. */
const root = import.meta.dir;
const allowed = new Set(['/index.html', '/style.css', '/app.js']);
const server = Bun.serve({
  hostname: '0.0.0.0', port: Number(Bun.env.PORT ?? 5187),
  fetch(request) {
    const path = new URL(request.url).pathname === '/' ? '/index.html' : new URL(request.url).pathname;
    if (!allowed.has(path)) return new Response('Not found', { status: 404 });
    return new Response(Bun.file(root + path), { headers: { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' } });
  },
});
console.log(`Browser example: http://localhost:${server.port}`);
