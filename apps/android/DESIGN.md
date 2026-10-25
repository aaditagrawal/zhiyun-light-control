# Lighttime Android interface

Lighttime is a pocket controller for the MOLUS G60. It connects directly over
Bluetooth, with no account, bridge address, Wi-Fi setup or token.

## Art direction

The interface is lit, not dark. Every screen sits on a full-height crimson
radial canvas (`canvas` in `src/ui/theme.ts`): a hot `#DF2121` core falling
through oxblood `#610B0A` to ember `#150505` at the edges. Darks and surfaces
stay red-brown (hue 355–8°); never use plum or burgundy-violet.

Type is white `#FFFFFF` with pink `#F2D2D1` secondary text; there is no cream
body text. Poppins (Regular, Medium, SemiBold) sets all UI text. Playfair
Display sets at most one centred headline per screen. Both are bundled under
`assets/fonts` and registered by `App.tsx` with the exact aliases in
`font` (`theme.ts`). Do not use Android `serif`, Roboto or letter-spaced
all-caps eyebrow lines.

Materials come from `src/pattern-ui/materials.tsx`:

- **Jelly glass** panels: near-black centres, thick pale frosted rims brightest
  on the lower edge, a fine top highlight and a soft drop shadow. A 1 px
  hairline is never an edge definition. Squircle radii of 28–36 dp.
- **Frosted glass** for circular top-bar buttons, chips and secondary actions.
- **Obsidian** glossy black capsules with pale end blooms for primary actions.
  Red is reserved for light: rings, glows, the power orb and active states.
- **Neon wells**: black recessed circles with concentric glowing red rings
  around a white value disc.

Compose in depth: lit backdrop, back panels, the G60 or a card deck, then the
foreground controls. The G60 renders (`assets/g60/*-ruby.png`) are lit by
scarlet and pale studio light and illustrate the fixture, never its live state.

## Words

Text-light, UI-heavy. Main tabs carry about a dozen short labels plus actual
values. Labels live inside objects. Explanations sit behind the circular **?**
button, which opens a short inline sheet (Android Back closes it). Transient
status uses one short line: a draft, a pending request, a confirmation that
clears itself, or a failure that stays until resolved.

## Guided setup and discovery

The welcome screen leads through three steps. Returning users can choose
Find my light to enter the same permission and discovery path directly,
without automatic reconnection or stored-device assumptions:

1. Power on the G60 and release its connection to ZY Vega or another controller.
2. Enable Android Bluetooth and explain the Nearby devices permission before
   requesting it. Android 11 and earlier need their platform-specific Location
   permission and Location setting for Bluetooth scanning; no location is collected.
3. Discover in Lighttime, choose the correct G60, identify it and read its actual
   output. A verified reading leads to a ready screen before the control view.

The connection happens **in Lighttime**. No Android settings pairing, pairing
code or account is required. Opening Bluetooth settings only enables the radio.

Discovery is a dedicated screen. Its scanning, results, empty, denied, blocked,
Bluetooth-off, unavailable and connection-error states come from `useLight`.
List only actual discovered lights, with their advertised name and signal.
Never auto-select an arbitrary result or substitute demonstration discovery.
A stronger signal is useful guidance, not proof of the device's identity.

Keep Back, Cancel, retry and troubleshooting reachable. Leaving discovery
cancels an active scan or connection before returning to setup; cancellation
also invalidates a pending permission result. Android Back follows the same
route history. The ready screen requires a verified hardware reading.

## Connected destinations

The top bar is a 48 dp glass **?** circle, a centred Poppins Medium title and a
glass connection circle that opens Device. The bottom navigation is a dark glass
bar with **Light**, **Scenes** and **Device**; the active item is a copper-rim
pill with a red under-glow and its label, the others are linework glyphs.
Open the main panels on first use, then retain them. Switching destinations
only changes visibility, preserving the connection, scene selection and tuning
form. `PatternPanel` stays mounted across tabs and connection help.

While a scene runs, a glass bar under the top bar on every tab shows its state and
holds the obsidian **Stop & restore** action. On Light it adds that a manual
adjustment stops the scene.

### Light

Use generous spacing around the instruments and their soft light falloff.
The page scrolls naturally; do not compress the controls to fit one fold.
Keep navigation and the running scene's Stop action outside the scroll view.
Content scrolls beneath the glass header and navigation through soft fades.
Recovery errors and their retry action precede the product hero.
Use static multi-stop gradients for broad, gradual light rather than live blur
filters, and animate only transform or opacity for decorative motion.

- The G60 sits on a stacked back deck above the main jelly panel.
- The brightness instrument is a neon well: a lit tick arc for the actual
  reading, concentric rings whose glow follows actual brightness, and a white
  disc with the actual percentage. A pending or dragged request is a white
  ghost tick, never a change to the actual arc or disc.
- Below it, the native accessible slider runs in a moulded black groove,
  flanked by glass − and + buttons (5% or 100 K steps).
- A small glass chip shows Verified, Checking or Unverified. A red orb in the
  panel corner is the power switch: lit when the light reads back on, dark glass
  when it reads back off.
- White balance is a recessed tick-ruler trough, warm to cool, with the actual
  Kelvin value and an indicative swatch above it. Its colours illustrate the
  temperature; they are not calibrated light measurements.

Instrument numerals may bound font scaling to fit their shape but keep full
accessibility labels. Body text and labels scale; at 320 dp width or 2× text
the page scrolls and remains usable.

### Values and writes

The instrument always shows actual readback. While dragging, the slider keeps
a separate draft and writes once on release. Accessibility and keyboard
adjustments commit a single step without a touch release. − and + commit one
request each. Pending requests lock the control. Distinguish pending,
confirmed and failed confirmation. Off means 0% brightness, not mains power;
On restores the prior output according to the controller's rules.

Readback failures stay visible beside a Read light again action and lock
adjustments until verification succeeds.

### Device

A rim-lit circle holds the G60, then the device name in Playfair, a connection
chip, three stat columns (brightness, white balance, reading time), a glass
read action, selectable identity details, and an obsidian Disconnect.

### 3D showcase

The G60 artwork on Welcome, Light and Device opens a fullscreen, offline model
viewer. A visible glass **3D** badge marks the interaction. It works before
pairing. Drag to turn and tilt, pinch to zoom, or use Front, Back and Reset;
a separate spin control pauses or starts the turntable. The default view fits
the whole object, and deliberate zoom permits closer inspection.

Use the same scarlet studio light, soft canvas fades and crisp native controls.
The LCD is blank in this viewer; it never displays sample settings that could
pass for device readback. The model is an illustration, not live output.

Load the renderer only when opened, directly from the bundled APK asset. Keep
the large page out of React Native view properties. Batch static geometry, cap pixel density,
draw only during motion and release the WebView when closed. Backgrounding
pauses rendering. Respect Android reduced motion on opening. Keep the loading
WebView visible beneath the still artwork: setting its opacity to zero can
prevent Android from initializing the renderer. At large text sizes, stack
the view buttons inside a scrollable footer. Real loading progress renews a
20-second stall window, within a 60-second total active-time limit. Background
time does not consume either limit. A load failure
keeps the still artwork and provides retry and Close; contain module failures
without unmounting the Bluetooth owner. Keep a running scene's Stop action and
any restoration error visible inside the showcase.

## Scenes

Use visual scene choices and previews sampled from the shared SDK program,
with tuning progressively disclosed. Label previews as previews. Selecting or
editing a scene must not change the light until Start. Running status and errors
come from the actual runner. Stop & restore restores the values captured when
that run started. Manual output changes stop the running scene. An unverified
reading shows inline with its retry action above the scenes.

## Accessibility

Touch targets are at least 48 dp. Text is at least 13 dp; interactive labels at
least 14 dp. No text sits on bright glow without a dark local backing. Decorative
labels truncate rather than wrap one glyph per line. Glyphs are drawn with
views, not icon fonts.

## Runtime boundaries

Preserve the SDK's serialized writes, operation gate, verified readback, effect
ownership and retained cleanup retries. A timeout does not establish that native
work has stopped. Backgrounding stops the scene and closes Bluetooth while the
light keeps its last output. Screen navigation must not own connection teardown.

Fixture screenshots validate layout and interaction presentation only. Android
build checks validate packaging. Report physical BLE behavior only when measured
with a real G60; an emulator or fixture cannot establish hardware operation.
