# Android layout fixture

Render the actual Android components with React Native Web for fast visual
inspection. This uses a separate fixture hook, safe-area and slider shims;
it never accesses Bluetooth. It does not validate native BLE operation.

```sh
cd scripts/android-ui-qa
bun install --frozen-lockfile
bun run build
bun run serve
```

Open port 18925. `?mode=welcome` is the default; additional fixture states are
`connected`, `nearby`, `multiple`, `scanning`, `empty`, `connecting`,
`permission`, `denied`, `offline`, `unsupported`, `working`, `running` and `error`.
Set `api=30` to inspect legacy permission guidance; API 35 is the default.
The server binds all interfaces for private-device review. Stop it with Ctrl+C.

Welcome, Light and Device automatically show the real rotating model in a
sandboxed iframe, with direct drag/zoom, pause and optional fullscreen expansion.
Native Android uses `react-native-webview`; the adapter exercises the same
offline document and command/status bridge, including the transparent inline
mode. `?viewerFail=module` simulates an unavailable viewer module to check that
its local error boundary preserves the app beneath.
