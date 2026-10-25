import { resolve } from 'node:path';

const base = import.meta.dir;
const repo = resolve(base, '../..');
const result = await Bun.build({
  entrypoints: [resolve(base, 'entry.tsx')], outdir: resolve(base, 'dist'),
  target: 'browser', publicPath: '/', loader: { '.png': 'file', '.webp': 'file', '.ttf': 'file' },
  define: { __DEV__: 'false', 'process.env.NODE_ENV': '"development"' },
  plugins: [{ name: 'fixture-only-modules', setup(build) {
    build.onResolve({ filter: /.*/ }, ({ path }) => {
      if (path === 'react') return { path: resolve(base, 'node_modules/react/index.js') };
      if (path.startsWith('react/')) return { path: resolve(base, 'node_modules', `${path}.js`) };
      if (path === 'react-dom/client') return { path: resolve(base, 'node_modules/react-dom/client.js') };
      if (path === 'react-native') return { path: resolve(base, 'node_modules/react-native-web/dist/index.js') };
      if (path === '@react-native-community/slider') return { path: resolve(base, 'slider.tsx') };
      if (path === 'react-native-safe-area-context' || path === 'expo-status-bar') return { path: resolve(base, 'safe.tsx') };
      if (path === 'expo-font') return { path: resolve(base, 'font.tsx') };
      if (path === 'react-native-webview') return { path: resolve(base, 'webview.tsx') };
      if (path === './src/useLight') return { path: resolve(base, 'hook.ts') };
      if (path === '@lighttime/sdk') return { path: resolve(repo, 'packages/sdk/src/index.ts') };
      return undefined;
    });
  } }],
});
if (!result.success) { console.error(result.logs); process.exit(1); }
await Bun.write(resolve(base, 'dist/index.html'), '<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lighttime Android UI fixture — no hardware</title><div id="root"></div><script type="module" src="/entry.js"></script></html>');
console.log('Rendered app source with a fixture hook and web slider. No Bluetooth or hardware access.');
