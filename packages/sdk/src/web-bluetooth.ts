import { BleProtocolTransport } from './transport';
import type { BleAdapter, BleTransportOptions } from './transport';
import { DisconnectedError, LighttimeError } from './errors';
import type { Unsubscribe } from './types';

export const G60_SERVICE = '0000fee9-0000-1000-8000-00805f9b34fb';
export const G60_WRITE = 'd44bc439-abfd-45a2-b575-925416129600';
export const G60_NOTIFY = 'd44bc439-abfd-45a2-b575-925416129601';
// Structural Web Bluetooth types avoid requiring browser-specific ambient types in RN.
interface Characteristic extends EventTarget {
  value?: DataView;
  writeValueWithResponse(bytes: Uint8Array<ArrayBuffer>): Promise<void>;
  startNotifications(): Promise<Characteristic>;
  stopNotifications(): Promise<Characteristic>;
}
interface GattServer {
  connected: boolean;
  connect(): Promise<GattServer>;
  disconnect(): void;
  getPrimaryService(uuid: string): Promise<{ getCharacteristic(uuid: string): Promise<Characteristic> }>;
}
export interface WebBluetoothDevice extends EventTarget { id: string; name?: string; gatt?: GattServer }
export interface BluetoothChooser {
  requestDevice(options: { filters: { namePrefix: string }[]; optionalServices: string[] }): Promise<WebBluetoothDevice>;
}
class WebAdapter implements BleAdapter {
  private writeCharacteristic: Characteristic | undefined;
  private notifyCharacteristic: Characteristic | undefined;
  private notificationsNeedStop = false;
  private disposed = false;
  constructor(private device: WebBluetoothDevice) {}
  get address(): string { return this.device.id; }
  async connect(): Promise<void> {
    if (!this.device.gatt) throw new LighttimeError('unsupported', 'Selected device has no GATT server');
    const server = await this.device.gatt.connect();
    this.ensureOpen();
    const service = await server.getPrimaryService(G60_SERVICE);
    this.ensureOpen();
    this.writeCharacteristic = await service.getCharacteristic(G60_WRITE);
    this.ensureOpen();
    this.notifyCharacteristic = await service.getCharacteristic(G60_NOTIFY);
    this.ensureOpen();
  }
  private ensureOpen(): void {
    if (this.disposed) { this.device.gatt?.disconnect(); throw new DisconnectedError(); }
  }
  async write(bytes: Uint8Array): Promise<void> {
    this.ensureOpen();
    if (!this.writeCharacteristic) throw new DisconnectedError();
    await this.writeCharacteristic.writeValueWithResponse(new Uint8Array(bytes));
  }
  async subscribe(listener: (bytes: Uint8Array) => void): Promise<Unsubscribe> {
    const characteristic = this.notifyCharacteristic;
    if (!characteristic) throw new DisconnectedError();
    const changed = () => {
      const value = characteristic.value;
      if (value && !this.disposed) listener(new Uint8Array(value.buffer, value.byteOffset, value.byteLength).slice());
    };
    characteristic.addEventListener('characteristicvaluechanged', changed);
    const cleanup = () => {
      characteristic.removeEventListener('characteristicvaluechanged', changed);
    };
    // Stop notifications as part of the awaited adapter close, after native writes
    // and setup have settled. The synchronous unsubscribe only removes callbacks.
    this.notificationsNeedStop = true;
    try { await characteristic.startNotifications(); this.ensureOpen(); return cleanup; }
    catch (error) { cleanup(); throw error; }
  }
  onDisconnect(listener: () => void): Unsubscribe {
    this.device.addEventListener('gattserverdisconnected', listener);
    return () => this.device.removeEventListener('gattserverdisconnected', listener);
  }
  async disconnect(): Promise<void> {
    this.disposed = true;
    if (this.notificationsNeedStop && this.notifyCharacteristic) {
      try { await this.notifyCharacteristic.stopNotifications(); }
      catch (error) {
        // Link loss already ends notifications. Still await the submitted stop,
        // but retain failures for retry whenever GATT remains live or unknown.
        if (this.device.gatt?.connected !== false) throw error;
      }
      this.notificationsNeedStop = false;
    }
    this.device.gatt?.disconnect();
  }
}
export class WebBluetoothTransport extends BleProtocolTransport {
  static available(): boolean {
    return typeof navigator !== 'undefined' && Boolean((navigator as Navigator & { bluetooth?: unknown }).bluetooth) && globalThis.isSecureContext === true;
  }
  /** Invoke directly in a click/tap handler, before any await. Chooser cancellation is a browser error. */
  static async request(options: BleTransportOptions = {}, bluetooth?: BluetoothChooser): Promise<WebBluetoothTransport> {
    const chooser = bluetooth ?? (typeof navigator !== 'undefined' ? (navigator as Navigator & { bluetooth?: BluetoothChooser }).bluetooth : undefined);
    if (!chooser || (!bluetooth && globalThis.isSecureContext !== true)) {
      throw new LighttimeError('unsupported', 'Direct BLE needs Web Bluetooth in a secure supported Chromium browser. Use the bridge otherwise.');
    }
    const device = await chooser.requestDevice({ filters: [{ namePrefix: 'PL103' }], optionalServices: [G60_SERVICE] });
    return new WebBluetoothTransport(new WebAdapter(device), options);
  }
}
