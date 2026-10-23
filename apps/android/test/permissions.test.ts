import { expect, test } from 'bun:test';
import { requiredPermissions } from '../src/ble/permissions';

test('Android 12+ requires both Nearby devices permissions and no location permission', () => {
  for (const api of [31, 32, 33, 34, 35, 36, 37]) {
    expect(requiredPermissions(api)).toEqual(['android.permission.BLUETOOTH_SCAN', 'android.permission.BLUETOOTH_CONNECT']);
  }
});

test('Android 6–11 requests the older scan location permission only', () => {
  for (const api of [23, 24, 29, 30]) expect(requiredPermissions(api)).toEqual(['android.permission.ACCESS_FINE_LOCATION']);
});

test('pre-runtime-permission Android versions do not show runtime permission dialogs', () => {
  expect(requiredPermissions(22)).toEqual([]);
});
