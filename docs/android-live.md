# Android phone validation

Date: 2026-10-23. The user authorized USB ADB installation and testing of the
Lighttime preview on the attached phone, including the app's BLE permissions.

## Current result

**Blocked before installation: no Android phone is visible to ADB.** The initial
`adb devices -l` result and a bounded 12-second USB-device wait both returned
an empty device list. This is not an unauthorized-device result: ADB has not
listed a phone on which to accept an RSA prompt. Check the USB data connection
and enable USB debugging on the intended phone before resuming.

The isolated workspace ADB is available: Android Debug Bridge 1.0.41,
platform-tools 37.0.1. The preview APK's SHA-256 was verified:

```text
b981eb0633ae7770e9f972a8314086f6d40b543a9e2d26609d2e74f6233ef3a0
```

Artifact: `artifacts/android-build/lighttime-g60-arm64-preview.apk`, package
`dev.lighttime.android`, arm64 release with an embedded Hermes bundle and
public debug/test signing. See [build evidence](android-build.md).
This is the rebuilt effects preview; the final USB check still listed no phone.

Installation, launch, permission dialogs, native BLE discovery, connection,
brightness, color temperature and reconnect have **not** been validated on a
physical Android phone. No phone packages or data have been changed. The
laptop bridge, Bluetooth services and existing G60 connection were untouched.

## Resume the authorized test

1. List attached devices using the isolated ADB under
   `artifacts/android-build/toolchains/android-sdk/platform-tools/adb`.
   Select only the actual attached USB phone. If it is unauthorized, report
   the phone's RSA prompt and stop rather than polling indefinitely.
2. On an authorized phone, record model, Android release/API and CPU ABIs;
   verify `arm64-v8a` support. Install this APK with `install -r` and launch
   `dev.lighttime.android`. Do not uninstall an existing app or clear its data.
3. Use UI Automator and screenshots to check startup and permission UI.
   Permission requests begin with **Find my light**. Granting the request
   immediately continues into scanning, so wait for the parent controller's
   explicit BLE handoff before accepting the prompt. If the required BLE
   permissions are already granted, do not press **Find my light** before
   that handoff. Grant only the BLE permissions requested for the phone's API
   level; do not grant unrelated permissions.
4. After the parent releases the laptop connection and explicitly hands off
   BLE, scan, connect, record initial brightness/CCT, and validate bounded
   changes with completed device readback. Exercise disconnect/reconnect and
   restore the recorded initial settings before handing BLE back.

## Local evidence

Logs, UI XML and screenshots belong only under ignored
`artifacts/android-live/`. The directory is private to the workspace account.
It initially contains `adb-version.txt`, `adb-devices-initial.txt`, and
`apk-sha256.txt`. Do not publish raw phone serials, addresses, account data,
notification contents or unrelated application screens in this document.
