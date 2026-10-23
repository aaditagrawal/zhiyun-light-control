# Android UI review response — 2026-10-24

The P3 finding in [the review](android-ui-review.md) is fixed before the private
APK build. `PatternPanel` owns raw frame drafts independently of converted
numeric configuration. Collapsing tuning no longer destroys those strings;
switching to another scene deliberately resets them. Invalid time, brightness
and temperature remain invalid until edited, rather than silently becoming a
valid carry-forward value.

Three component regressions cover collapse/reopen, repair and scene reset.
The Android suite passes **40 tests / 1394 assertions**, and TypeScript passes.

Root reran the original browser-fixture reproduction: clear Frame 2's time,
collapse Tune pattern and reopen it. The input remains empty, Start stays
disabled, and no `NaN` appears. Entering `2.5` restores the SDK preview and
enables Start. The fixture renders the actual components with a fake connection
hook and web slider shim; this is UI behavior evidence, not physical BLE
validation.
