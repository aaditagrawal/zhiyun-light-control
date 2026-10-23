import type { BleAdapter, Unsubscribe } from '@lighttime/sdk';
import type { BleManager, Device, Subscription } from 'react-native-ble-plx';
import { fromByteArray, toByteArray } from 'base64-js';
import { G60_NOTIFY, G60_SERVICE, G60_WRITE } from './constants';

/** Only native GATT I/O lives here; framing, correlation and verification belong to the SDK. */
export class NativeBleAdapter implements BleAdapter {
  private device: Device | null = null;
  private closed = false;
  private started = false;
  private dropped = false;
  private monitor: Subscription | null = null;
  private nativeDisconnect: Subscription | null = null;
  private readonly listeners = new Set<() => void>();

  constructor(private readonly manager: BleManager, readonly address: string) {}

  onDisconnect(listener: () => void): Unsubscribe {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  async connect(): Promise<void> {
    if (this.started || this.closed) throw new Error('Create a new BLE adapter to reconnect.');
    this.started = true;
    try {
      const device = await this.manager.connectToDevice(this.address, { autoConnect: false, timeout: 10_000 });
      this.device = device;
      if (this.closed) {
        await this.manager.cancelDeviceConnection(this.address);
        throw new Error('Connection was cancelled.');
      }
      this.nativeDisconnect = this.manager.onDeviceDisconnected(this.address, () => this.drop());
      await device.discoverAllServicesAndCharacteristics();
      if (this.closed || this.dropped) throw new Error('The light disconnected during discovery.');
      const services = await device.services();
      if (!services.some((service) => service.uuid.toLowerCase() === G60_SERVICE)) {
        throw new Error('Unsupported light: the G60 control service is missing.');
      }
      const characteristics = await device.characteristicsForService(G60_SERVICE);
      const write = characteristics.find((item) => item.uuid.toLowerCase() === G60_WRITE);
      const notify = characteristics.find((item) => item.uuid.toLowerCase() === G60_NOTIFY);
      if (!write?.isWritableWithResponse || !(notify?.isNotifiable || notify?.isIndicatable)) {
        throw new Error('Unsupported light: G60 write or notification characteristics are unavailable.');
      }
      if (this.closed || this.dropped) throw new Error('Connection was cancelled.');
    } catch (error) {
      await this.disconnect().catch(() => {});
      throw error;
    }
  }

  async subscribe(listener: (bytes: Uint8Array) => void): Promise<Unsubscribe> {
    const device = this.requireDevice();
    if (this.monitor) throw new Error('Notifications are already subscribed.');
    const subscription = device.monitorCharacteristicForService(G60_SERVICE, G60_NOTIFY, (error, characteristic) => {
      if (this.closed || this.dropped) return;
      if (error) {
        this.drop();
        void this.manager.cancelDeviceConnection(this.address).catch(() => {});
        return;
      }
      if (characteristic?.value) {
        try { listener(toByteArray(characteristic.value).slice()); }
        catch {
          this.drop();
          void this.manager.cancelDeviceConnection(this.address).catch(() => {});
        }
      }
    });
    if (this.dropped || this.closed) {
      subscription.remove();
      throw new Error('The notification subscription could not be started.');
    }
    let active = true;
    const owned = { remove: () => {
      if (active) { active = false; subscription.remove(); }
    } };
    this.monitor = owned;
    return () => {
      owned.remove();
      if (this.monitor === owned) this.monitor = null;
    };
  }

  async write(bytes: Uint8Array): Promise<void> {
    const device = this.requireDevice();
    // SDK splits frames into <=20-byte writes, which work at the default ATT MTU.
    if (bytes.length > 20) throw new Error('BLE writes must be split into 20-byte chunks.');
    await device.writeCharacteristicWithResponseForService(G60_SERVICE, G60_WRITE, fromByteArray(bytes));
    this.requireDevice();
    // ATT acknowledgement is not a proprietary protocol reply. Queries alone wait for replies.
  }

  async disconnect(): Promise<void> {
    this.closed = true;
    this.drop();
    if (this.started) {
      try { await this.manager.cancelDeviceConnection(this.address); }
      catch (error) {
        if (await this.manager.isDeviceConnected(this.address).catch(() => false)) throw error;
      }
    }
  }

  private requireDevice(): Device {
    if (!this.device || this.closed || this.dropped) throw new Error('The light is disconnected.');
    return this.device;
  }

  private drop(): void {
    if (this.dropped) return;
    this.dropped = true;
    this.device = null;
    this.monitor?.remove();
    this.monitor = null;
    this.nativeDisconnect?.remove();
    this.nativeDisconnect = null;
    for (const listener of this.listeners) {
      try { listener(); } catch { /* One observer must not prevent native cleanup. */ }
    }
    this.listeners.clear();
  }
}
