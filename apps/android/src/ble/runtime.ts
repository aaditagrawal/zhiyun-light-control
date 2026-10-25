import Constants, { ExecutionEnvironment } from 'expo-constants';
import { PermissionsAndroid, Platform } from 'react-native';
import type { BleManager } from 'react-native-ble-plx';
import { requiredPermissions } from './permissions';

export function runtimeIssue(): string | null {
  if (Platform.OS !== 'android') return 'Direct Bluetooth control is available in the Android app.';
  if (Constants.executionEnvironment === ExecutionEnvironment.StoreClient) {
    return 'Install the Lighttime development build. Expo Go does not include the native Bluetooth module.';
  }
  return null;
}

export function createBleManager(): BleManager {
  // A type-only import above and a guarded require keep Expo Go/non-Android error screens usable.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { BleManager: Manager } = require('react-native-ble-plx') as typeof import('react-native-ble-plx');
  return new Manager();
}

export type PermissionResult = 'granted' | 'denied' | 'blocked';

export async function requestBlePermissions(): Promise<PermissionResult> {
  const permissions = requiredPermissions(Number(Platform.Version));
  if (!permissions.length) return 'granted';
  const results = await PermissionsAndroid.requestMultiple(permissions);
  if (permissions.some((permission) => results[permission] === PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN)) return 'blocked';
  return permissions.every((permission) => results[permission] === PermissionsAndroid.RESULTS.GRANTED) ? 'granted' : 'denied';
}
