# Local hardware observations, 2026-10-23

- Host: validation laptop, Fedora Linux 45, normal desktop user.
- The first USB enumeration had no light, but it subsequently appeared as
  USB `fff8:0180`, `Zhiyun Tech Zhiyun Virtual ComPort`, serial `00000000001A`.
- Linux already bound `cdc_acm` and `usb-storage`; serial device `/dev/ttyACM0`.
- Stable path `/dev/serial/by-id/usb-Zhiyun_Tech_Zhiyun_Virtual_ComPort_00000000001A-if00`.
- Serial device is root:dialout 0660, user has no ACL and is not in dialout.
  Passwordless sudo is unavailable. No serial commands have been sent yet.
- Bluetooth adapter on. Observed `PL103_EDFE`, random address
  `F3:58:E9:18:35:EA`, mesh proxy service `1828`.
- Manufacturer `0x0905`, payload `706c313033000808a409e0c1100113`.
- Mesh proxy service data `00b61fd28efc8de2ab` (network ID advertisement).
- User authorizes local hardware experiments and built-in camera captures.
- Baseline camera image saved to `artifacts/local/camera-baseline.jpg`.
  It shows a desk and bags, with bright glare at bottom right; does not yet
  provide an unobstructed view of the complete lamp or a readable display.
- Do not flash firmware or reset Bluetooth provisioning merely for discovery.

## Subsequent verified results

- Serial ACL was granted with user-authorized sudo. USB identity and device-ID
  queries responded at 115200 baud; runtime brightness/CCT queries did not.
- Direct BLE FEE9 endpoints were enumerated and the proprietary ZYBL protocol
  recovered from the official downloaded Android app was verified on hardware.
- Device info: serial `08a409e0c1100113`, model `pl103`, protocol device ID `0`.
- BLE readback and laptop-camera captures verified brightness 2%, 12%, 0%, and
  2700 K / 6500 K changes. Setters require no ZYBL reply; query separately.
- The initial 5% / 5000 K was restored. Evidence is in
  `artifacts/hardware/report.json` and the five original camera JPEGs beside it.
- Camera images show room illumination changes clearly. The initial baseline
  camera framing did not show the entire lamp, but illumination is sufficient
  for relative brightness/color validation, not calibrated CCT measurement.
