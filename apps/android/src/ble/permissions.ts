import type { Permission } from 'react-native';

export function requiredPermissions(apiLevel: number): Permission[] {
  if (apiLevel >= 31) return ['android.permission.BLUETOOTH_SCAN', 'android.permission.BLUETOOTH_CONNECT'];
  if (apiLevel >= 23) return ['android.permission.ACCESS_FINE_LOCATION'];
  return [];
}
