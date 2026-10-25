const { withAndroidManifest } = require('expo/config-plugins');

// The upstream plugin can leave location unbounded. Keep pre-Android-12
// compatibility while requesting only Nearby devices on Android 12 and later.
module.exports = function withAndroidBle(config) {
  return withAndroidManifest(config, (mod) => {
    const manifest = mod.modResults.manifest;
    const overrides = {
      'android.permission.BLUETOOTH': { 'android:maxSdkVersion': '30' },
      'android.permission.BLUETOOTH_ADMIN': { 'android:maxSdkVersion': '30' },
      'android.permission.ACCESS_FINE_LOCATION': { 'android:maxSdkVersion': '30' },
      'android.permission.ACCESS_COARSE_LOCATION': { 'android:maxSdkVersion': '30' },
      'android.permission.BLUETOOTH_SCAN': { 'android:usesPermissionFlags': 'neverForLocation' },
      'android.permission.BLUETOOTH_CONNECT': {},
    };
    manifest['uses-permission'] ??= [];
    for (const [name, attributes] of Object.entries(overrides)) {
      let permission = manifest['uses-permission'].find((item) => item.$['android:name'] === name);
      if (!permission) {
        permission = { $: { 'android:name': name } };
        manifest['uses-permission'].push(permission);
      }
      Object.assign(permission.$, attributes);
    }
    manifest['uses-feature'] ??= [];
    if (!manifest['uses-feature'].some((item) => item.$['android:name'] === 'android.hardware.bluetooth_le')) {
      manifest['uses-feature'].push({ $: { 'android:name': 'android.hardware.bluetooth_le', 'android:required': 'false' } });
    }
    return mod;
  });
};
