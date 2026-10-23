# Plug-and-play independent review

Reviewed 2026-10-24. Bounded to the new CLI defaults, automatic connection
lifecycle, SDK defaults, static module packaging and site example. No hardware,
live services, dependency changes or implementation edits.

1. **[P2] Whitespace-only addresses bypass the promised blank-address rejection.**
   `src/lighttime/bridge.py:439` checks length without checking `address.strip()`.
   `POST /v1/connect` with `{"address":" "}` or `{"address":"\t"}` therefore
   reaches `_replace_device`, which stops effects and closes the current light
   before attempting the invalid identifier. Empty strings and null correctly
   return 400. Validate whitespace before any replacement work. This is the
   existing explicit-address branch, but it still violates the stated validation
   requirement for this integration path.

2. **[P2, already assigned to backend] Disconnect can finish before a queued
   automatic connection starts.** `src/lighttime/bridge.py:334` schedules the
   automatic owner for a later event-loop turn; `disconnect` at line 445 neither
   invalidates nor drains an owner that has not acquired `control_lock` yet.
   Concurrent HTTP connect/disconnect requests reproduce a successful disconnect
   response containing `connected: false`, followed by `GET /v1/state` returning
   `connected: true`. Likewise, calling disconnect immediately after `startup`
   returns lets the queued startup task connect afterward. The existing test
   waits until discovery already holds the lock and misses this ordering.
   This confirms the known assigned issue; no duplicate implementation was made.

Offline reproduction for both findings, from the checkout:

```sh
uv run --frozen --no-sync --offline python - <<'PY'
import asyncio
from aiohttp.test_utils import TestClient, TestServer
import lighttime.bridge as module
from lighttime.bridge import Bridge, MockTransport

async def discover(timeout=5):
    return [{"transport": "ble", "name": "PL103_OFFLINE", "address": "OFFLINE"}]

async def main():
    module.discover = discover
    module.BleTransport = MockTransport
    async with TestClient(TestServer(Bridge(auto_connect=False).app)) as http:
        connected, disconnected = await asyncio.gather(
            http.post("/v1/connect", json={}),
            http.post("/v1/disconnect", json={}),
        )
        print("connect:", connected.status, (await connected.json())["connected"])
        print("disconnect:", disconnected.status, (await disconnected.json())["connected"])
        print("after disconnect:", (await (await http.get("/v1/state")).json())["connected"])
        for address in [" ", "\t"]:
            response = await http.post("/v1/connect", json={"address": address})
            print("blank:", repr(address), response.status)

asyncio.run(main())
PY
```

Observed: connect `200 True`; disconnect `200 False`; subsequent state `True`;
both whitespace requests `200`. MockTransport deliberately accepts the identifier
to expose the missing validation without touching Bluetooth.

Verification: 37 tests passed in `tests/test_plug_play.py` and
`tests/test_bridge_shutdown.py`; two focused TypeScript tests passed for the
zero-configuration client and optional tokens. Additional offline cancellation
check confirmed that canceling an HTTP device-request waiter preserves the owned
startup task, which completes after discovery releases. Existing focused tests
passed for bounded startup attempts, scanner cleanup, zero/multiple candidate
rejection and connection reuse with fresh readback while effects continue.

A fresh wheel built offline without build isolation included the current
`static/sdk.js`. Importing the extracted wheel independently of the checkout
served that module with HTTP 200, `application/javascript` and `no-store` while
using a separate custom web root. The assigned module-cache issue is resolved
in the reviewed snapshot. No further material SDK-default, packaging or example
integration findings.
