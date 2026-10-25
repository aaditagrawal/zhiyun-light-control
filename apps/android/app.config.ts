import type { ExpoConfig } from 'expo/config';

const config: ExpoConfig = {
  name: 'Lighttime',
  slug: 'lighttime-android',
  version: '0.3.0-preview.3',
  scheme: 'lighttime',
  orientation: 'portrait',
  userInterfaceStyle: 'dark',
  platforms: ['android'],
  android: {
    package: 'dev.lighttime.android',
    versionCode: 6,
    // Native BLE is the only transport. No location data, media, or camera access.
    blockedPermissions: [
      'android.permission.RECORD_AUDIO',
      'android.permission.CAMERA',
      'android.permission.READ_EXTERNAL_STORAGE',
      'android.permission.WRITE_EXTERNAL_STORAGE',
      'android.permission.ACCESS_BACKGROUND_LOCATION',
    ],
  },
  plugins: [
    ['react-native-ble-plx', {
      isBackgroundEnabled: false,
      neverForLocation: true,
      bluetoothAlwaysPermission: false,
    }],
    './plugins/with-android-ble.cjs',
    './plugins/with-lighttime-art.cjs',
  ],
};

export default config;
