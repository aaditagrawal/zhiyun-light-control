export const G60_SERVICE = '0000fee9-0000-1000-8000-00805f9b34fb';
export const G60_WRITE = 'd44bc439-abfd-45a2-b575-925416129600';
export const G60_NOTIFY = 'd44bc439-abfd-45a2-b575-925416129601';

export interface DiscoveredLight {
  id: string;
  name: string;
  rssi: number | null;
}

export function advertisedLight(device: {
  id: string; name: string | null; localName: string | null; rssi: number | null;
}): DiscoveredLight | null {
  const name = [device.localName, device.name].find((value) => value?.toUpperCase().startsWith('PL103'));
  return name ? { id: device.id, name, rssi: device.rssi } : null;
}
