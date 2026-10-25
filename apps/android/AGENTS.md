# Android client

Read [DESIGN.md](DESIGN.md) before changing the interface. Keep the native
discovery flow, indicative brightness/white-balance controls, and scene previews
consistent. Discovery and direct control need no account, bridge address or
bearer token.

Use the shared SDK for validation, sampling, verified writes and effect
ownership. Keep requested previews separate from real readback. Preserve
`EffectOwnership`, retained cleanup retries and the operation gate when changing
screens; a timeout does not prove native work has stopped.

Use the pinned Bun and frozen lock. Normal checks are `bun test`,
`bun run typecheck` and `bun run export:android`. A BLE-capable native build is
required; Expo Go does not provide the app's Bluetooth module. Compile a
standalone release for private phone testing and preserve its signing key and
package ID across updates. Increase Android `versionCode` for every published
artifact. Emulator/fixture UI checks do not establish physical BLE operation.
