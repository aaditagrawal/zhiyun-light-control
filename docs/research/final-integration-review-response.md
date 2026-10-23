# Final integration corrections

All three reproduced findings in [the final review](final-integration-review.md)
are addressed.

- Ordinary direct-runner handoffs cancel scheduling separately from admitted
  controller I/O. The existing transaction finishes and drains before manual
  output, baseline restoration or replacement baseline capture. Explicit cancel
  and disposal still abort all owned calls, including superseded ones. Five new
  real controller/transport cases cover all three handoffs and both destructive
  cleanup paths, including spacing and native-close ordering.
- Android's effect-operation `finally` releases its busy UI after settlement
  while mounted, even when background/disconnect changed the connection epoch.
  Three regressions execute the actual hook body with local hook cells and the
  real SDK stack. Start/background, Stop/background and Start/disconnect all end
  disconnected with `working: false`. They import no native runtime and use no
  hardware.
- Web Bluetooth still awaits submitted notification shutdown. A settled stop
  rejection is accepted when GATT is confirmed disconnected; pending shutdown
  stays owned, and failures on a live link remain retryable. Three regressions
  cover remote loss, live rejection/retry and pending-stop ownership.

Final offline suites: **123 SDK tests / 3,151 assertions**, **24 Android tests /
204 assertions**, TypeScript checks, ESM/declarations and public import smoke.
The unchanged 30 shared configurations retain 691 matching targets. These
checks do not establish physical Web Bluetooth or Android operation.
