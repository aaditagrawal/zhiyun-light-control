# OBS Studio integration

Lighttime controls the external MOLUS G60 through the local HTTP bridge. Load
the reusable Python script for sliders, hotkeys, and program-scene presets, or
open the existing browser controller as an OBS dock. Neither approach needs a
native OBS plugin, a camera source, or the OBS SDK: OBS is a client of the same
bridge used by other applications. The bridge owns the Bluetooth connection.

The script uses only `obspython` and Python's standard library. It deliberately
does not import the Lighttime package into OBS's embedded Python. Its source
uses Python 3.8-compatible syntax; select the Python version and architecture
supported by your OBS build. The bridge runs separately with the project's
Python 3.13+ environment. OBS documents Python setup, Tools → Scripts, lifecycle
hooks, and timer callbacks in its [official scripting documentation](https://docs.obsproject.com/scripting).

## Start the bridge

From the repository root:

```sh
uv sync --frozen
uv run lighttime serve --host 127.0.0.1 --port 8877
```

The bridge creates its token file and prints the **path**, without printing the
token. `uv run lighttime token-path` also reports the default path. Normally it
is `~/.config/lighttime/bridge.token` on Linux (honoring `XDG_CONFIG_HOME`),
`~/Library/Application Support/lighttime/bridge.token` on macOS, and
`%APPDATA%/lighttime/bridge.token` on Windows. `serve --token-file PATH` overrides
it. Use the file path from the running bridge if it uses an override.

Find your light's address using the browser controller's discovery controls or
the bridge's authenticated `GET /v1/devices?timeout=5` endpoint. macOS uses a
CoreBluetooth identifier instead of a Bluetooth MAC address. The OBS script
accepts that platform identifier verbatim. It does not scan or open Bluetooth
itself. You can also start the bridge with `--address YOUR_DEVICE_IDENTIFIER`
to have the bridge connect at startup.

## Load the Python script

1. Open **Tools → Scripts** in OBS. If required, configure **Python Settings**
   with a matching Python installation, as described by OBS's scripting docs.
2. Add [`integrations/obs/lighttime_obs.py`](../integrations/obs/lighttime_obs.py).
   A single copy of this file is sufficient; it has no sibling-module dependencies.
3. Set **Local bridge URL** to `http://127.0.0.1:8877` and select the bridge's
   token file. The file can contain the UTF-8 token alone, or a JSON object with
   a `token` string field. It is read on the worker thread for every request.
   A selected file overrides the password input. Alternatively paste the token
   in **Bearer token**; OBS saves this masked input in its settings as plaintext,
   so prefer the protected token file and leave the input empty.
4. Enter **BLE address / platform identifier**, then click **Connect BLE via
   bridge**. If the bridge already has the correct light connected, use
   **Refresh verified state**. Connecting or disconnecting affects all clients
   sharing this bridge.
5. Change **Brightness (%)** from 0–100 in whole-percent steps or **CCT (K)**
   from 2700–6500 in 100 K steps. A changed slider sends only its own field;
   **Apply both sliders** explicitly sends
   both displayed values. Opening the script reads state without applying the
   saved slider values, unless program-scene presets were explicitly enabled.
6. Use **Soft off** to send `{"power": false}` and **On / restore brightness**
   to send `{"power": true}`. The bridge implements off as brightness zero and
   remembers the last nonzero brightness for the current controller's lifetime.
   This is soft dimming, not mains power; reconnecting or restarting the bridge
   can reset that restore value. Moving brightness above zero also turns the
   light back on.

The sliders show your requested settings. They are not overwritten by bridge
readback, hotkeys, scene presets, or another client. The status message shows
verified readback returned by the bridge. Click **Show latest status** to update
the message in the Scripts dialog; changes and failures are also logged live
in **Help → Log Files → View Current Log** with a `Lighttime:` prefix. Refresh
state after another client changes the light. The script does not open SSE or
continuously poll the physical light.

In **Settings → Hotkeys**, assign **Lighttime: On / restore brightness**,
**Lighttime: Soft off**, and **Lighttime: Apply brightness and CCT sliders**.
Only key-down events issue controls. Bindings are saved and loaded with the
script; unload removes the timer, frontend callback, and hotkeys. The Python
scripting API unregisters a hotkey by its callback, rather than the C API's
numeric ID. These signatures are documented under
[scripting API differences](https://docs.obsproject.com/scripting#other-differences-from-the-c-api).

## Optional program-scene presets

Enter a JSON object in **Scene presets**, then enable **Enable program-scene
presets**. Names match the active program scene exactly:

```json
{
  "Interview": { "brightness": 35, "cct": 4500 },
  "Product closeup": { "brightness": 60, "cct": 5600 },
  "Break": { "power": false }
}
```

Enabling or editing a valid mapping applies the current program scene. With
presets enabled in saved settings, loading the script also applies the current
program scene. In Studio Mode, preview changes do not apply presets; changing
the program scene does. The script uses the active-scene reference and releases
it as required by the [OBS frontend API](https://docs.obsproject.com/reference-frontend-api#c.obs_frontend_get_current_scene).

Each mapped scene replaces the pending preset in full. Moving to an unmapped
scene, disabling presets, or changing scene collections cancels a pending scene
preset. Manual controls replace a pending scene preset. Leaving a scene leaves
the current physical settings in place; there is no automatic restore job.
Unknown fields, invalid ranges/resolutions, and contradictory power/brightness
values reject the mapping and disable presets until corrected. A request already sent to the
bridge may finish after a scene switch, including a switch to an unmapped scene.
It cannot be undone; the latest mapped scene's queued preset follows it. Old
responses update status only and never enqueue restoration or slider changes.

The G60 quantizes fractional brightness and intermediate CCT values. Presets
and worker controls therefore reject fractional percentages and temperatures
that are not multiples of 100 K before making a request. For example, 5.5% and
4501 K are rejected rather than silently accepted as verified 5% and 4500 K.
Wire-format brightness remains float32; public control resolution is 1%/100 K.

## Browser dock

Use the TypeScript controller in `examples/browser`; no second UI is maintained
by this integration. Build its JavaScript using the SDK package's Bun script:

```sh
cd packages/sdk
bun install --frozen-lockfile
bun run build:browser
```

That writes `examples/browser/app.js` beside `index.html` and `style.css`.
From the repository root, start the bridge with that directory explicitly:

```sh
uv run lighttime serve --host 127.0.0.1 --port 8877 --web-root examples/browser
```

Packaged bridge builds serve their bundled UI by default, so a normal
`lighttime serve` can use those assets without a checkout or Bun installation.
The explicit web root above selects the freshly built checkout example. A
custom distribution can instead pass an absolute web-root path containing its
built index and assets.

In **Docks → Custom Browser Docks**, add a dock named **Lighttime** with URL:

```text
http://127.0.0.1:8877/ui/index.html
```

The bridge root `http://127.0.0.1:8877/` redirects there when the built index is
available; otherwise it shows a bridge-running message. Static assets are
served under `/ui/`. If the dock gives a 404, build the example first, verify
the web-root directory, then restart the bridge so it registers the static
route. Use the controller's bridge mode and its token input, without placing
credentials in the dock URL. Same-origin hosting on port 8877 needs no extra
CORS origin flag. A separate development origin must be explicitly allowed
with the bridge's `--allow-origin` setting. Do not use a Browser Source in the
scene to expose your control panel or credentials on-stream.

Custom Browser Docks depend on OBS's
[browser component](https://github.com/obsproject/obs-browser#building) and
platform/build support; the Python script remains usable when that menu is
unavailable. The OBS project's
[dock dialog text](https://github.com/obsproject/obs-studio/blob/master/frontend/data/locale/en-US.ini)
documents entering a dock name and URL, then clicking Apply or Close.

The script uses these actual endpoints from [the shared contract](api-contract.md):

- `GET /v1/state` for initial/manual readback.
- `POST /v1/connect` with `{"transport":"ble","address":"…"}`.
- `POST /v1/disconnect` with an empty JSON object.
- `PATCH /v1/state` with changed `brightness`, `cct`, or `power` fields.

Every request carries `Authorization: Bearer …` in its header. The shared
browser client also uses `GET /v1/devices?timeout=5` and authenticated fetch
streaming from `GET /v1/events`. `GET /v1/health` is public. Static UI routes
contain no embedded token. The bridge returns typed State objects and reports
hardware/verification errors; the OBS script validates their shape before
showing success.

## Threading, failures, and validation

OBS property callbacks, hotkeys, and scene callbacks only update local queues.
A single worker performs token-file reads and HTTP requests. Slider changes
settle for 150 ms and merge into one pending patch; connection/refresh actions
have one pending slot, with the latest queued action replacing its predecessor.
Connect/disconnect clears older pending controls. Changing the bridge URL,
token source, or timeout clears queued work and old results, cancels its active
request, and reads the new bridge state. It does not replay the sliders.

The default HTTP deadline is 60 seconds, configurable from 1–60 seconds in OBS.
Real G60 reconnections took 10–17 seconds during validation; Bluetooth recovery
and the subsequent identity/readback queries need time to finish. An existing
OBS configuration retains its saved deadline, so increase it if necessary.
A worker-side deadline interrupts stalled sockets, including slow response
bodies. Shutdown signals cancellation and returns without waiting for network
I/O; the worker finishes cleanup separately. No OBS API is called from either
background thread. Requests use loopback HTTP origins only, including `::1`;
`localhost` resolves directly to `127.0.0.1`. Redirects, URL credentials,
queries, and fragments are refused. The transport bypasses HTTP proxy settings.

Authentication errors, HTTP failures, malformed/oversized responses, connection
failures, and timeouts produce warning status messages. Raw server bodies,
headers, URLs, credentials, and exception strings are never logged. After a
timeout, the actual light state may be unknown because a server operation can
outlive its HTTP client. Refresh before retrying; the script does not silently
retry writes or report cached state as verified.

Run the focused tests from the repository root:

```sh
uv run --frozen python -m unittest discover -s tests -p test_obs.py -v
uv run --frozen ruff check integrations/obs tests/test_obs.py
uv run --frozen ruff format --check integrations/obs tests/test_obs.py
```

Tests use a local fake HTTP server and a stub `obspython`. They cover imports,
coalescing during active requests, supported-resolution validation before writes,
authentication, token-file rotation, error redaction, redirects, deadlines,
cancellation/shutdown, hotkey ownership, scene
invalidation, and visible status reporting. No real OBS session, dock, Bluetooth,
camera, or light is exercised. During implementation, `obs` was unavailable on
PATH and the Flatpak application list contained no OBS installation. Real OBS
and cross-platform runtime validation remain manual checks on an OBS-equipped
machine; Python 3.8 syntax compatibility alone does not certify that runtime.
