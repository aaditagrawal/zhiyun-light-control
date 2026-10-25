# Lighttime agent index

## Integrating a website or application

Start with [docs/integrating-sites.md](docs/integrating-sites.md). It includes
a copy-paste integration brief, complete small examples and deployment choices.
Run the local bridge with `uv run --frozen lighttime`; `uv run` prepares the
environment, so a separate sync step is unnecessary. The browser controller is
at `http://localhost:8877/`.

- The bridge binds loopback port 8877, requires no token by default and discovers
  one nearby PL103/G60 at startup. Multiple candidates require explicit
  selection; never select the first arbitrary scan result.
- `POST /v1/connect` with `{}` reuses an existing connection with fresh readback,
  or discovers the single G60. Python: `BridgeClient()` then `await light.connect()`.
  TypeScript: `new BridgeClient()` then `await light.connect()`.
- Serve a static site using `serve --web-root PATH`. Import
  `{ BridgeClient, EFFECT_PRESETS }` from `/lighttime.js` without a build step,
  and construct the client with `{ baseUrl: location.origin }`.
- For a bundled app, build `packages/sdk` and install its local directory or
  packed archive with Bun. `@lighttime/sdk` is not published to npm.
- The SDK's default URL is `http://127.0.0.1:8877`. Use the page's origin when
  served by the bridge, or a configured reachable private bridge URL for a
  remote app. A phone's localhost refers to that phone, not the bridge laptop.
- Keep requested settings, actual readback and errors separate. Brightness is
  whole-percent 0–100; CCT is 2700–6500 K in 100 K steps. Successful setters
  require device readback. Soft off is brightness zero, not mains power.
- Share the bridge's BLE owner across clients. `dispose()` closes a TypeScript
  client's requests and streams; it does not disconnect shared hardware or stop
  a server effect. Explicit manual writes cancel the running effect.

Optional deployment controls are `serve --auth`, `--token-file PATH`, exact
`--allow-origin ORIGIN` values, `--address ADDRESS` and `--no-auto-connect`.
`--no-auth` remains compatible. The normal integration has no token-entry flow.
Private HTTPS and proxy arrangements are described in the integration guide.

## Repository map

| Area | Entry points |
| --- | --- |
| Runtime and HTTP shapes | [docs/api-contract.md](docs/api-contract.md) |
| Python SDK, CLI and deployment | [docs/python.md](docs/python.md), `src/lighttime/` |
| TypeScript SDK and browser | [docs/typescript.md](docs/typescript.md), `packages/sdk/src/`, `examples/browser/` |
| Minimal static-site integration | `examples/site-controller/index.html`, [website guide](docs/integrating-sites.md) |
| Shared effects | [docs/effects-contract.md](docs/effects-contract.md), [program fields](docs/effects-programs-contract.md), [behavior](docs/effects.md) |
| OBS and Android | [docs/obs.md](docs/obs.md), [docs/android.md](docs/android.md) |
| USB support and limits | [docs/usb.md](docs/usb.md) |
| Hardware evidence and history | [docs/validation.md](docs/validation.md), `docs/research/`, [migration](docs/overhaul.md) |

## Working in this checkout

Use uv for Python environments, dependencies, scripts and tools, preserving the
project Python pin and lockfile. Run project scripts as `uv run --frozen ...`;
use `uv run --no-project python script.py` for standalone scripts, and `uvx` or
`uv tool run` for standalone tools. Use the project-pinned Ruff and ty.

Use Bun for JavaScript/TypeScript installs, scripts and tests. Preserve existing
lockfiles and package-manager pins: `bun install --frozen-lockfile`,
`bun run <script>` and `bun test`. Prefer installed executables in
`~/.local/bin` and `~/.bun/bin`; resolve a missing command before retrying.
Keep environments isolated, preserve active virtual environments and reuse
package caches and frozen locks. Keep existing runtime requirements until a
migration is validated. Node remains available for tools requiring it; validate
compatibility before using `bun --bun` on a Node-targeted script. Compile
bytecode when installing frequently launched Python tools. Dependency
audits are `uv audit --frozen` and `bun audit` in the relevant package; use
`pip-audit` for installed Python distributions. Keep TLS and integrity checks
enabled. Measure representative timings before claiming a speed improvement.

For Python changes, the normal checks are `uv run --frozen pytest -q`,
`uv run --frozen ruff check src tests scripts integrations/obs`, and
`uv run --frozen ty check`. For SDK changes, use its `bun test`,
`bun run typecheck`, `bun run build` and `bun run smoke` scripts. Rebuild the
web assets with `bun scripts/build-web.ts` from the repository root after SDK
or browser-source changes. This canonical command rebuilds the controller and
`/lighttime.js` and copies them into the Python static directory so packaging
and CI comparisons stay aligned. The no-build site example imports the module
served by the bridge. Check local links and contract examples for documentation
changes.

Normal tests use mocks and local test servers. Physical validation scripts and
the live SDK smoke script write to a real light; report physical results only
when measured. Preserve historical research and validation records as dated
evidence; describe current defaults in the current guides and contracts.
