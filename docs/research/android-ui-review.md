# Android UI review — 2026-10-24

Reviewed the `0.2.0-preview.1` / Android versionCode 2 interface before private
APK publication. No supported publication-blocking defect was found in this
bounded UI review. One low-priority defect is reproducible below.

## Supported finding

### [P3] Reopening Tune pattern replaces an incomplete frame time with `NaN`

- **Location:** [PatternPanel.tsx:60–62](../../apps/android/src/PatternPanel.tsx#L60),
  [KeyframeEditor.tsx:17–19](../../apps/android/src/pattern-ui/KeyframeEditor.tsx#L17),
  and [keyframes.ts:6–16](../../apps/android/src/pattern-ui/keyframes.ts#L6).
- **Reproduction:** In the connected fixture, open Patterns → Sequence → Scene
  sequence → Tune pattern. Clear Frame 2's time. The blank input correctly
  invalidates the preview and disables Start. Collapse Tune pattern, then reopen
  it. Frame 2's input now contains the literal text `NaN` instead of the blank
  draft. This was reproduced using the real components served at
  `http://localhost:18921/?mode=connected`.
- **Cause:** Collapsing tuning unmounts `KeyframeEditor`. Its raw string drafts
  live only in that component. The parent retains the converted frames, where
  an empty required time becomes `Number.NaN`; remounting reconstructs the
  input using `String(frame.at)`.
- **Impact:** A routine disclosure toggle corrupts the visible editing state
  and exposes an internal numeric sentinel. Start remains correctly disabled;
  no invalid write was observed. Replacing the field with `2` or `2.5` restores
  the preview and enables Start, so this is recoverable.
- **Action:** Preserve raw frame drafts above the disclosure boundary, or keep
  the editor mounted while tuning is hidden. Keep incomplete input invalid
  without round-tripping its display text through numeric frames. Verify the
  clear → collapse → reopen → correct flow after changing it.

## Validation and observations

Independently ran, from `apps/android`:

```sh
bun test src/ui/IndicativeSlider.test.ts test/pattern-preview.test.ts test/effect-form.test.ts
```

Result: **18 passed, 0 failed, 1,205 assertions**. These cover draft versus actual
values, release-only touch commits, pending-write suppression, unverified
readback, non-touch slider callbacks, all 16 default configurations, SDK sample
equality, timing windows, invalid intermediate values, and immutable presets.

The existing QA renderer was inspected in a separate private browser tab at
390 × 844, without rebuilding its bundle or changing its server. All 16 scenes
were selected through the UI; each had a labeled SDK preview and an enabled
Start action, while fake actual output stayed at 24% / 4300 K. Valid and blank
frame-time drafts both survived Controls → Patterns switching. Correcting an
invalid frame restored the preview; selecting a finite run exposed the restore
setting. Fixture Start/Stop displayed and cleared running controls.

Idle, nearby, permission-blocked, failed-readback, working, connecting, and
unsupported fixture modes were inspected. The discovery UI exposes explicit
selection and recovery actions. Working/unverified controls disable writes;
Disconnect stays available, and connecting exposes Cancel connection. Fixture
Disconnect removed the connected panels and returned to discovery. Static
inspection confirmed that reconnect actions still use the existing hook and
that effect start/stop and restore settings retain their existing wiring.

Root additionally reported **37 Android tests / 1,376 assertions**, clean
TypeScript and Hermes export checks, all 16 selections without page overflow
at 320 px, and 15 keyframe tuning inputs without overflow. Those are
root-reported results, not additional checks run by this reviewer.

## Scope and limits

Review covered `App.tsx`, `src/ui/*`, `src/PatternPanel.tsx`,
`src/pattern-ui/*`, and the focused UI/form tests. Hook signatures and callbacks
were read only to establish UI integration. The unchanged SDK, native adapter,
ownership/drain implementation, and prior runner-handoff audit were not
re-audited.

The renderer uses fake `useLight` state and a web slider shim. Its checks establish
component behavior, not native permission dialogs, TalkBack/gesture ordering,
BLE pairing, asynchronous native cleanup/reconnection, or physical restoration.
No hardware or service operation, build, dependency installation, git command,
or source edit was performed. Only this report was written.
