// Print an expression for T3 preview_evaluate in a fresh about:blank tab.
// No server, browser driver, DOM shim, dependencies, bridge, or hardware needed.
const root = new URL('../../../', import.meta.url);
const build = await Bun.build({ entrypoints: [new URL('./regression.js', import.meta.url).pathname], target: 'browser' });
if (!build.success) throw new Error(build.logs.join('\n'));
const html = await Bun.file(new URL('examples/browser/index.html', root)).text();
const bundlePath = Bun.argv[2] ?? 'examples/browser/app.js';
const bundle = await Bun.file(new URL(bundlePath, root)).text();
const harness = await build.outputs[0]!.text();
console.log(`(async () => {\n${harness}\nreturn await globalThis.lighttimeBrowserRegression(${JSON.stringify({ html, bundle })});\n})()`);
