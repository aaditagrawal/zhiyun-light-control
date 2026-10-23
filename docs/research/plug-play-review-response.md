# Plug-and-play review corrections

Both findings in [the review](plug-play-review.md) are resolved.

Whitespace-only identifiers now fail validation before device replacement, so
an invalid request preserves the active connection and effect. Disconnect
synchronously invalidates older automatic attempts before waiting for locks.
Generation guards remain active across discovery, identification and readback,
including backends that swallow cancellation. Superseded requests receive an
API error; a later explicit connection remains allowed.

The original concurrent connect/disconnect and blank-address reproductions pass.
Frozen checks: 452 Python tests plus 34 subtests, including 37 plug-and-play
cases; Ruff and ty pass. The website's 66 browser cases cover address-free
retry and deliberate multi-light selection. Normal client cancellation still
leaves an accepted shared connection attempt owned by the bridge.
