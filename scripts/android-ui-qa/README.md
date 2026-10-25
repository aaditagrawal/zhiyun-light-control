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

The **3D** badge opens the actual offline viewer in a sandboxed iframe. Native
Android uses `react-native-webview`; the iframe adapter exercises the same
document and command/status bridge. `?viewerFail=module` simulates an unavailable
viewer module to check that its local error boundary preserves the app beneath.
