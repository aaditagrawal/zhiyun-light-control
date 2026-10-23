# Lighttime Android interface

Lighttime is a pocket light controller. Finding the G60 and changing its output
should take a few taps, without a server address, account or token.

The direction follows the owner's recent native app guidance: quiet charcoal
surfaces, system typography, tonal controls and clear status beside each task.
Use safe-area insets, scalable text and touch targets of at least 48 dp. Let
controls wrap on narrow screens rather than shrinking their labels.

## Discovery

Start with one **Find my light** action. Request Android's Bluetooth permission
when needed, show nearby G60s with their signal strength, then let the user
choose one. Keep cancel, retry and app-settings recovery available. A successful
connection includes identification and a fresh reading of the actual output.
The normal flow does not require pairing in Android settings or a passkey.

## Controls

Brightness is a luminous fader with a clear percentage. White balance is a
warm-to-cool strip with Kelvin landmarks and a matching color swatch. These
colors illustrate the requested temperature; they are not calibrated light
measurements. Keep the native accessible slider as the input control.

Show a draft value while dragging and send it when released. Actual readings
remain separate, and confirmation follows hardware readback. Power, readback
recovery and connection actions must remain easy to reach.

## Patterns

Use visual scene choices and a preview sampled from the shared SDK program,
with tuning progressively disclosed. Clearly label previews as previews.
Selecting or editing a pattern must not change the light until **Start**.
Running status and errors come from the actual runner. **Stop & restore**
restores the values captured when that run started.

## Runtime boundaries

UI changes must preserve the SDK's serialized writes, verified readback and
owned cleanup. Backgrounding stops the pattern and closes Bluetooth; the light
keeps its last settings. Never substitute a demonstration connection when the
native module or real G60 is unavailable.
