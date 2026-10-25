// src/types.ts
var EFFECT_PATTERNS = Object.freeze([
  "blink",
  "breathe",
  "fade-in",
  "fade-out",
  "cct-sweep",
  "pulse",
  "flicker",
  "sunrise",
  "lightning",
  "emergency",
  "paparazzi",
  "tv",
  "morse",
  "keyframes"
]);
function disconnectedState() {
  return {
    connected: false,
    transport: null,
    address: null,
    device: null,
    brightness: null,
    cct: null,
    power: null,
    updatedAt: null
  };
}
function validatePatch(patch) {
  if (Object.keys(patch).some((key) => !["brightness", "cct", "power"].includes(key))) {
    throw new TypeError("Only brightness, cct, and power are supported");
  }
  if (patch.brightness !== undefined && (!Number.isInteger(patch.brightness) || patch.brightness < 0 || patch.brightness > 100)) {
    throw new RangeError("G60 brightness must be a whole percent between 0 and 100");
  }
  if (patch.cct !== undefined && (!Number.isInteger(patch.cct) || patch.cct % 100 !== 0 || patch.cct < 2700 || patch.cct > 6500)) {
    throw new RangeError("G60 CCT must be a multiple of 100 between 2700 and 6500 K");
  }
  if (patch.power !== undefined && typeof patch.power !== "boolean")
    throw new TypeError("Power must be boolean");
  if (patch.power !== undefined && patch.brightness !== undefined && patch.power !== patch.brightness > 0) {
    throw new RangeError("Brightness and power must agree");
  }
}
// src/errors.ts
class LighttimeError extends Error {
  code;
  constructor(code, message, options) {
    super(message, options);
    this.code = code;
    this.name = new.target.name;
  }
}

class TimeoutError extends LighttimeError {
  constructor(message = "Operation timed out") {
    super("timeout", message);
  }
}

class CancelledError extends LighttimeError {
  constructor() {
    super("cancelled", "Operation cancelled");
  }
}

class DisconnectedError extends LighttimeError {
  constructor(message = "Light is disconnected") {
    super("disconnected", message);
  }
}

class ProtocolError extends LighttimeError {
  constructor(message) {
    super("protocol", message);
  }
}

class VerificationError extends LighttimeError {
  constructor(message) {
    super("verification_failed", message);
  }
}

class BridgeError extends LighttimeError {
  status;
  constructor(code, message, status) {
    super(code, message);
    this.status = status;
  }
}
// src/protocol.ts
var COMMAND = { info: 8195, deviceId: 8197, brightness: 4097, cct: 4098 };
var REQUEST_TYPE = 256;
var RESPONSE_TYPE = 1;
function crc16Xmodem(bytes) {
  let crc = 0;
  for (const byte of bytes) {
    crc ^= byte << 8;
    for (let bit = 0;bit < 8; bit++)
      crc = (crc << 1 ^ (crc & 32768 ? 4129 : 0)) & 65535;
  }
  return crc;
}
function uint16(value) {
  if (!Number.isInteger(value) || value < 0 || value > 65535)
    throw new RangeError("Expected uint16");
}
function encodeFrame(frame) {
  uint16(frame.messageType);
  uint16(frame.sequence);
  uint16(frame.command);
  const length = frame.payload.length + 6;
  uint16(length);
  const bytes = new Uint8Array(length + 6);
  const view = new DataView(bytes.buffer);
  bytes.set([36, 60]);
  view.setUint16(2, length, true);
  view.setUint16(4, frame.messageType, true);
  view.setUint16(6, frame.sequence, true);
  view.setUint16(8, frame.command, true);
  bytes.set(frame.payload, 10);
  view.setUint16(length + 4, crc16Xmodem(bytes.subarray(4, length + 4)), true);
  return bytes;
}

class FrameParser {
  maxBodyLength;
  buffer;
  used = 0;
  corruptFrames = 0;
  constructor(maxBodyLength = 1024) {
    this.maxBodyLength = maxBodyLength;
    if (!Number.isInteger(maxBodyLength) || maxBodyLength < 6 || maxBodyLength > 65535)
      throw new RangeError("Invalid maximum frame length");
    this.buffer = new Uint8Array(maxBodyLength + 6);
  }
  reset() {
    this.used = 0;
  }
  push(chunk) {
    const frames = [];
    for (const byte of chunk) {
      this.buffer[this.used++] = byte;
      while (this.used >= 2) {
        if (this.buffer[0] !== 36 || this.buffer[1] !== 60) {
          this.drop();
          continue;
        }
        if (this.used < 4)
          break;
        const view = new DataView(this.buffer.buffer);
        const length = view.getUint16(2, true);
        if (length < 6 || length > this.maxBodyLength) {
          this.corruptFrames++;
          this.drop();
          continue;
        }
        if (this.used < length + 6)
          break;
        if (crc16Xmodem(this.buffer.subarray(4, length + 4)) !== view.getUint16(length + 4, true)) {
          this.corruptFrames++;
          this.drop();
          continue;
        }
        frames.push({
          messageType: view.getUint16(4, true),
          sequence: view.getUint16(6, true),
          command: view.getUint16(8, true),
          payload: this.buffer.slice(10, length + 4)
        });
        this.buffer.copyWithin(0, length + 6, this.used);
        this.used -= length + 6;
      }
    }
    return frames;
  }
  drop() {
    this.buffer.copyWithin(0, 1, this.used);
    this.used--;
  }
}
function valuePayload(command, deviceId, value) {
  uint16(deviceId);
  if (command !== COMMAND.brightness && command !== COMMAND.cct)
    throw new ProtocolError("Unsupported value command");
  if (value !== undefined)
    validatePatch(command === COMMAND.brightness ? { brightness: value } : { cct: value });
  const bytes = new Uint8Array(command === COMMAND.brightness ? 7 : 5);
  const view = new DataView(bytes.buffer);
  view.setUint16(0, deviceId, true);
  bytes[2] = value === undefined ? 0 : 1;
  if (command === COMMAND.brightness)
    view.setFloat32(3, value ?? 0, true);
  else
    view.setUint16(3, value ?? 0, true);
  return bytes;
}
function decodeValue(frame, deviceId) {
  const expected = frame.command === COMMAND.brightness ? 7 : frame.command === COMMAND.cct ? 5 : 0;
  if (!expected || frame.payload.length !== expected)
    throw new ProtocolError("Unexpected value payload length");
  const view = new DataView(frame.payload.buffer, frame.payload.byteOffset, frame.payload.byteLength);
  if (view.getUint16(0, true) !== deviceId || view.getUint8(2) !== 0)
    throw new ProtocolError("Unexpected device ID or read mode");
  const value = frame.command === COMMAND.brightness ? view.getFloat32(3, true) : view.getUint16(3, true);
  if (!Number.isFinite(value) || (frame.command === COMMAND.brightness ? value < 0 || value > 100 : value < 2700 || value > 6500)) {
    throw new ProtocolError("Device returned an out-of-range value");
  }
  return value;
}
// src/async.ts
function validateTimeout(ms) {
  if (!Number.isFinite(ms) || ms <= 0 || ms > 300000)
    throw new RangeError("timeoutMs must be 1–300000");
}
function deadline(options = {}, fallback = 5000, lifecycle) {
  const ms = options.timeoutMs ?? fallback;
  validateTimeout(ms);
  const controller = new AbortController;
  let reason = new CancelledError;
  const stop = (error) => {
    if (!controller.signal.aborted) {
      reason = error;
      controller.abort();
    }
  };
  const abort = () => stop(new CancelledError);
  const disconnected = () => stop(new DisconnectedError);
  const timer = setTimeout(() => stop(new TimeoutError), ms);
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted)
    abort();
  lifecycle?.addEventListener("abort", disconnected, { once: true });
  if (lifecycle?.aborted)
    disconnected();
  return {
    signal: controller.signal,
    check() {
      if (controller.signal.aborted)
        throw reason;
    },
    async run(work) {
      if (controller.signal.aborted) {
        work.catch(() => {});
        throw reason;
      }
      return new Promise((resolve, reject) => {
        const onAbort = () => {
          controller.signal.removeEventListener("abort", onAbort);
          reject(reason);
        };
        controller.signal.addEventListener("abort", onAbort, { once: true });
        work.then(resolve, reject).finally(() => controller.signal.removeEventListener("abort", onAbort));
      });
    },
    close() {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", abort);
      lifecycle?.removeEventListener("abort", disconnected);
      stop(new CancelledError);
    }
  };
}

class SerialQueue {
  tail = Promise.resolve();
  run(task) {
    const next = this.tail.then(task);
    this.tail = next.catch(() => {});
    return next;
  }
  async drain() {
    while (true) {
      const tail = this.tail;
      await tail;
      if (tail === this.tail)
        return;
    }
  }
}

class OwnedWork {
  pending = new Set;
  track(work) {
    this.pending.add(work);
    work.then(() => this.pending.delete(work), () => this.pending.delete(work));
    return work;
  }
  async drain() {
    while (this.pending.size)
      await Promise.allSettled([...this.pending]);
  }
}
async function drainWithin(work, timeoutMs, subject) {
  const op = deadline({}, timeoutMs);
  try {
    await op.run(work);
  } catch (error) {
    if (error instanceof TimeoutError)
      throw new TimeoutError(`${subject} timed out; unresolved work remains owned. Retry drain() or disconnect() before replacing the session.`);
    throw error;
  } finally {
    op.close();
  }
}
function throwIfAborted(signal) {
  if (signal.aborted)
    throw new CancelledError;
}
function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new CancelledError);
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(new CancelledError);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

// src/transport.ts
class BleProtocolTransport {
  adapter;
  status = "new";
  parser;
  writes = new SerialQueue;
  work = new OwnedWork;
  pending = new Map;
  listeners = new Set;
  lifecycle = new AbortController;
  unnotify;
  undrop;
  sequence = 0;
  timeoutMs;
  maxWriteSize;
  closing;
  needsClose = false;
  constructor(adapter, options = {}) {
    this.adapter = adapter;
    this.parser = new FrameParser(options.maxBodyLength);
    this.timeoutMs = options.timeoutMs ?? 5000;
    validateTimeout(this.timeoutMs);
    this.maxWriteSize = options.maxWriteSize ?? 20;
    if (!Number.isInteger(this.maxWriteSize) || this.maxWriteSize < 1 || this.maxWriteSize > 512)
      throw new RangeError("Invalid BLE write size");
  }
  get connected() {
    return this.status === "connected";
  }
  get address() {
    return this.adapter.address;
  }
  onDisconnect(listener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  async connect(options = {}) {
    if (this.status !== "new")
      throw new DisconnectedError("Use a new transport for each connection");
    const op = deadline(options, this.timeoutMs, this.lifecycle.signal);
    this.status = "connecting";
    try {
      op.check();
      this.undrop = this.adapter.onDisconnect(() => this.drop());
      const setup = this.work.track((async () => {
        await this.adapter.connect();
        op.check();
        if (this.status !== "connecting")
          throw new DisconnectedError;
        const unsubscribe = await this.adapter.subscribe((bytes) => this.receive(bytes));
        this.unnotify = unsubscribe;
        if (this.status !== "connecting") {
          this.releaseSubscriptions();
          throw new DisconnectedError;
        }
        op.check();
      })());
      await op.run(setup);
      op.check();
      if (this.status !== "connecting")
        throw new DisconnectedError;
      this.status = "connected";
    } catch (error) {
      this.drop();
      this.closeAdapter().catch(() => {});
      throw error;
    } finally {
      op.close();
    }
  }
  disconnect() {
    this.drop();
    return drainWithin(this.closeAdapter(), this.timeoutMs, "BLE disconnect");
  }
  drain() {
    return drainWithin((async () => {
      await this.work.drain();
      if (this.needsClose)
        await this.closeAdapter();
      else
        await this.closing;
    })(), this.timeoutMs, "BLE drain");
  }
  closeAdapter() {
    if (this.closing)
      return this.closing;
    const closing = (async () => {
      await this.work.drain();
      this.releaseSubscriptions();
      await this.adapter.disconnect();
      this.needsClose = false;
    })();
    this.closing = closing;
    closing.catch(() => {
      if (this.closing === closing)
        this.closing = undefined;
    });
    return closing;
  }
  drop() {
    if (this.status === "closed")
      return;
    this.status = "closed";
    this.needsClose = true;
    this.lifecycle.abort();
    this.parser.reset();
    try {
      this.releaseSubscriptions();
    } catch {}
    for (const entry of this.pending.values())
      entry.reject(new DisconnectedError);
    this.pending.clear();
    for (const listener of this.listeners) {
      try {
        listener();
      } catch {}
    }
    this.listeners.clear();
  }
  releaseSubscriptions() {
    let failure;
    try {
      this.unnotify?.();
      this.unnotify = undefined;
    } catch (error) {
      failure = error;
    }
    try {
      this.undrop?.();
      this.undrop = undefined;
    } catch (error) {
      failure ??= error;
    }
    if (failure !== undefined)
      throw failure;
  }
  receive(bytes) {
    if (this.status !== "connected")
      return;
    for (const frame of this.parser.push(bytes)) {
      const entry = this.pending.get(frame.sequence);
      if (frame.messageType === RESPONSE_TYPE && entry?.command === frame.command) {
        this.pending.delete(frame.sequence);
        entry.resolve(frame);
      }
    }
  }
  nextSequence() {
    if (!this.connected)
      throw new DisconnectedError;
    if (this.sequence === 65535)
      throw new ProtocolError("Sequence space exhausted; reconnect with a new transport");
    return ++this.sequence;
  }
  async send(command, payload, options = {}) {
    await this.exchange(command, payload, false, options);
  }
  async request(command, payload = new Uint8Array, options = {}) {
    return await this.exchange(command, payload, true, options);
  }
  async exchange(command, payload, reply, options) {
    const op = deadline(options, this.timeoutMs, this.lifecycle.signal);
    let sequence;
    let nativeWritePending = false;
    try {
      op.check();
      sequence = this.nextSequence();
      if (this.pending.size >= 64)
        throw new ProtocolError("Too many pending requests");
      const frame = encodeFrame({ messageType: REQUEST_TYPE, command, sequence, payload });
      if (frame.length > this.parser.maxBodyLength + 6)
        throw new ProtocolError("Frame exceeds transport maximum");
      let response;
      if (reply) {
        response = new Promise((resolve, reject) => this.pending.set(sequence, { command, resolve, reject }));
        response.catch(() => {});
      }
      const writing = this.work.track(this.writes.run(async () => {
        op.check();
        if (!this.connected)
          throw new DisconnectedError;
        for (let offset = 0;offset < frame.length; offset += this.maxWriteSize) {
          op.check();
          if (!this.connected)
            throw new DisconnectedError;
          try {
            nativeWritePending = true;
            await this.adapter.write(frame.slice(offset, offset + this.maxWriteSize));
            nativeWritePending = false;
            op.check();
            if (!this.connected)
              throw new DisconnectedError;
          } catch (error) {
            nativeWritePending = false;
            this.drop();
            this.closeAdapter().catch(() => {});
            throw error;
          }
        }
      }));
      if (response) {
        const [, result] = await op.run(this.work.track(Promise.all([writing, response])));
        op.check();
        if (!this.connected)
          throw new DisconnectedError;
        return result;
      }
      await op.run(writing);
      op.check();
      if (!this.connected)
        throw new DisconnectedError;
    } catch (error) {
      if (nativeWritePending) {
        this.drop();
        this.closeAdapter().catch(() => {});
      }
      throw error;
    } finally {
      if (sequence !== undefined) {
        this.pending.get(sequence)?.reject(new CancelledError);
        this.pending.delete(sequence);
      }
      op.close();
    }
  }
}
// src/controller.ts
class LightController {
  transport;
  queue = new SerialQueue;
  listeners = new Set;
  lifecycle = new AbortController;
  current = disconnectedState();
  restoreBrightness = 5;
  timeoutMs;
  attempts;
  delayMs;
  undrop;
  disconnecting;
  needsDisconnect = false;
  constructor(transport, options = {}) {
    this.transport = transport;
    this.timeoutMs = options.timeoutMs ?? 1e4;
    validateTimeout(this.timeoutMs);
    this.attempts = options.verificationAttempts ?? 3;
    this.delayMs = options.verificationDelayMs ?? 100;
    if (!Number.isInteger(this.attempts) || this.attempts < 1 || this.attempts > 10 || !Number.isFinite(this.delayMs) || this.delayMs < 0 || this.delayMs > 2000)
      throw new RangeError("Invalid verification policy");
    this.undrop = transport.onDisconnect(() => {
      this.lifecycle.abort();
      this.publish(disconnectedState());
    });
  }
  get state() {
    return { ...this.current, device: this.current.device ? { ...this.current.device } : null };
  }
  subscribe(listener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  publish(state) {
    this.current = state;
    for (const listener of this.listeners) {
      try {
        listener(this.state);
      } catch {}
    }
    return this.state;
  }
  operation(options, work) {
    const op = deadline(options, this.timeoutMs, this.lifecycle.signal);
    const queued = this.queue.run(async () => {
      op.check();
      await this.transport.drain?.();
      op.check();
      return work(op.signal);
    });
    return op.run(queued).finally(() => op.close());
  }
  connect(options = {}) {
    return this.operation(options, async (signal) => {
      try {
        await this.transport.connect({ signal, timeoutMs: this.timeoutMs });
        throwIfAborted(signal);
        const info = await this.transport.request(COMMAND.info, undefined, { signal });
        throwIfAborted(signal);
        const fields = asciiFields(info.payload);
        if (fields.length < 2 || fields[1]?.toLowerCase() !== "pl103")
          throw new ProtocolError("Device is not a confirmed PL103 / MOLUS G60");
        const id = await this.transport.request(COMMAND.deviceId, undefined, { signal });
        throwIfAborted(signal);
        if (id.payload.length !== 2)
          throw new ProtocolError("Invalid device ID response");
        const deviceId = new DataView(id.payload.buffer, id.payload.byteOffset, 2).getUint16(0, true);
        this.current = {
          ...disconnectedState(),
          connected: true,
          transport: "ble",
          address: this.transport.address,
          device: { model: fields[1], serial: fields[0], id: deviceId }
        };
        return await this.read(signal);
      } catch (error) {
        this.current = disconnectedState();
        throw error;
      }
    }).catch((error) => {
      this.disconnect().catch(() => {});
      throw error;
    });
  }
  invalidate() {
    this.lifecycle.abort();
    this.undrop();
    this.publish(disconnectedState());
  }
  closeTransport() {
    if (this.disconnecting)
      return this.disconnecting;
    const cleanup = (async () => {
      await this.queue.drain();
      await this.transport.drain?.();
      await this.transport.disconnect();
      this.needsDisconnect = false;
    })();
    this.disconnecting = cleanup;
    cleanup.catch(() => {
      if (this.disconnecting === cleanup)
        this.disconnecting = undefined;
    });
    return cleanup;
  }
  drain() {
    return drainWithin((async () => {
      await this.queue.drain();
      await this.transport.drain?.();
      if (this.needsDisconnect)
        await this.closeTransport();
      else
        await this.disconnecting;
    })(), this.timeoutMs, "Controller drain");
  }
  disconnect() {
    if (!this.disconnecting)
      this.needsDisconnect = true;
    this.invalidate();
    return drainWithin(this.closeTransport(), this.timeoutMs, "Controller disconnect").then(() => this.state);
  }
  refresh(options = {}) {
    return this.operation(options, (signal) => this.read(signal));
  }
  setState(patch, options = {}) {
    validatePatch(patch);
    const target = { ...patch };
    return this.operation(options, async (signal) => {
      const id = this.requireDevice();
      const brightness = target.brightness ?? (target.power === false ? 0 : target.power === true ? this.restoreBrightness : undefined);
      if (brightness !== undefined)
        await this.transport.send(COMMAND.brightness, valuePayload(COMMAND.brightness, id, brightness), { signal });
      throwIfAborted(signal);
      if (target.cct !== undefined)
        await this.transport.send(COMMAND.cct, valuePayload(COMMAND.cct, id, target.cct), { signal });
      throwIfAborted(signal);
      for (let attempt = 0;attempt < this.attempts; attempt++) {
        throwIfAborted(signal);
        if (attempt > 0)
          await pause(this.delayMs, signal);
        const actual = await this.read(signal);
        throwIfAborted(signal);
        if ((brightness === undefined || Math.abs(actual.brightness - brightness) <= 0.01) && (target.cct === undefined || actual.cct === target.cct))
          return actual;
      }
      throw new VerificationError(`Readback did not match requested settings (actual ${this.current.brightness}%, ${this.current.cct} K)`);
    });
  }
  requireDevice() {
    if (!this.transport.connected || !this.current.connected || !this.current.device)
      throw new DisconnectedError;
    return this.current.device.id;
  }
  async read(signal) {
    throwIfAborted(signal);
    const id = this.requireDevice();
    const dim = await this.transport.request(COMMAND.brightness, valuePayload(COMMAND.brightness, id), { signal });
    throwIfAborted(signal);
    const cct = await this.transport.request(COMMAND.cct, valuePayload(COMMAND.cct, id), { signal });
    throwIfAborted(signal);
    this.requireDevice();
    const brightness = decodeValue(dim, id);
    const kelvin = decodeValue(cct, id);
    if (brightness > 0)
      this.restoreBrightness = brightness;
    return this.publish({ ...this.current, brightness, cct: kelvin, power: brightness > 0, updatedAt: new Date().toISOString() });
  }
}
function asciiFields(payload) {
  const fields = [];
  let current = "";
  for (const byte of payload) {
    if (byte === 0) {
      if (current)
        fields.push(current);
      current = "";
    } else {
      if (byte < 32 || byte > 126)
        throw new ProtocolError("Non-ASCII device information");
      current += String.fromCharCode(byte);
    }
  }
  if (current)
    fields.push(current);
  return fields;
}
// src/web-bluetooth.ts
var G60_SERVICE = "0000fee9-0000-1000-8000-00805f9b34fb";
var G60_WRITE = "d44bc439-abfd-45a2-b575-925416129600";
var G60_NOTIFY = "d44bc439-abfd-45a2-b575-925416129601";

class WebAdapter {
  device;
  writeCharacteristic;
  notifyCharacteristic;
  notificationsNeedStop = false;
  disposed = false;
  constructor(device) {
    this.device = device;
  }
  get address() {
    return this.device.id;
  }
  async connect() {
    if (!this.device.gatt)
      throw new LighttimeError("unsupported", "Selected device has no GATT server");
    const server = await this.device.gatt.connect();
    this.ensureOpen();
    const service = await server.getPrimaryService(G60_SERVICE);
    this.ensureOpen();
    this.writeCharacteristic = await service.getCharacteristic(G60_WRITE);
    this.ensureOpen();
    this.notifyCharacteristic = await service.getCharacteristic(G60_NOTIFY);
    this.ensureOpen();
  }
  ensureOpen() {
    if (this.disposed) {
      this.device.gatt?.disconnect();
      throw new DisconnectedError;
    }
  }
  async write(bytes) {
    this.ensureOpen();
    if (!this.writeCharacteristic)
      throw new DisconnectedError;
    await this.writeCharacteristic.writeValueWithResponse(new Uint8Array(bytes));
  }
  async subscribe(listener) {
    const characteristic = this.notifyCharacteristic;
    if (!characteristic)
      throw new DisconnectedError;
    const changed = () => {
      const value = characteristic.value;
      if (value && !this.disposed)
        listener(new Uint8Array(value.buffer, value.byteOffset, value.byteLength).slice());
    };
    characteristic.addEventListener("characteristicvaluechanged", changed);
    const cleanup = () => {
      characteristic.removeEventListener("characteristicvaluechanged", changed);
    };
    this.notificationsNeedStop = true;
    try {
      await characteristic.startNotifications();
      this.ensureOpen();
      return cleanup;
    } catch (error) {
      cleanup();
      throw error;
    }
  }
  onDisconnect(listener) {
    this.device.addEventListener("gattserverdisconnected", listener);
    return () => this.device.removeEventListener("gattserverdisconnected", listener);
  }
  async disconnect() {
    this.disposed = true;
    if (this.notificationsNeedStop && this.notifyCharacteristic) {
      try {
        await this.notifyCharacteristic.stopNotifications();
      } catch (error) {
        if (this.device.gatt?.connected !== false)
          throw error;
      }
      this.notificationsNeedStop = false;
    }
    this.device.gatt?.disconnect();
  }
}

class WebBluetoothTransport extends BleProtocolTransport {
  static available() {
    return typeof navigator !== "undefined" && Boolean(navigator.bluetooth) && globalThis.isSecureContext === true;
  }
  static async request(options = {}, bluetooth) {
    const chooser = bluetooth ?? (typeof navigator !== "undefined" ? navigator.bluetooth : undefined);
    if (!chooser || !bluetooth && globalThis.isSecureContext !== true) {
      throw new LighttimeError("unsupported", "Direct BLE needs Web Bluetooth in a secure supported Chromium browser. Use the bridge otherwise.");
    }
    const device = await chooser.requestDevice({ filters: [{ namePrefix: "PL103" }], optionalServices: [G60_SERVICE] });
    return new WebBluetoothTransport(new WebAdapter(device), options);
  }
}
// src/effects.ts
var EFFECT_MIN_STEP = 0.1;
var TOLERANCE = 0.000000001;
var SEED_MAX = 4294967295;
var COMMON_FIELDS = [
  "pattern",
  "duration",
  "period",
  "minBrightness",
  "maxBrightness",
  "cct",
  "cctEnd",
  "restoreOnComplete",
  "seed",
  "curve"
];
var PATTERN_FIELDS = {
  blink: ["duty"],
  breathe: [],
  "fade-in": [],
  "fade-out": [],
  "cct-sweep": [],
  pulse: ["bpm", "style", "accentEvery", "accentRatio", "decay"],
  flicker: ["style", "depth", "speed", "gusts", "cctCoupling"],
  sunrise: ["direction"],
  lightning: ["interval", "flashesMax"],
  emergency: ["flashes", "flashOn", "flashOff", "gap"],
  paparazzi: ["interval", "burst"],
  tv: ["interval", "motion"],
  morse: ["text", "unit", "loop"],
  keyframes: ["keyframes", "loop"]
};
var CCT_DEFAULTS = {
  flicker: [2700, 3200],
  sunrise: [2700, 6500],
  lightning: [6500, 6500],
  emergency: [2700, 6500],
  paparazzi: [5600, 6500],
  tv: [5000, 6500]
};
var MORSE_CODE = Object.freeze({
  A: ".-",
  B: "-...",
  C: "-.-.",
  D: "-..",
  E: ".",
  F: "..-.",
  G: "--.",
  H: "....",
  I: "..",
  J: ".---",
  K: "-.-",
  L: ".-..",
  M: "--",
  N: "-.",
  O: "---",
  P: ".--.",
  Q: "--.-",
  R: ".-.",
  S: "...",
  T: "-",
  U: "..-",
  V: "...-",
  W: ".--",
  X: "-..-",
  Y: "-.--",
  Z: "--..",
  0: "-----",
  1: ".----",
  2: "..---",
  3: "...--",
  4: "....-",
  5: ".....",
  6: "-....",
  7: "--...",
  8: "---..",
  9: "----.",
  ".": ".-.-.-",
  ",": "--..--",
  "?": "..--..",
  "/": "-..-.",
  "=": "-...-",
  "+": ".-.-.",
  "-": "-....-",
  "@": ".--.-."
});
function record(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function bounded(value, min, max) {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
}
function pattern(value) {
  return EFFECT_PATTERNS.includes(value);
}
function number(name, value, min, max) {
  if (!bounded(value, min, max))
    throw new RangeError(`${name} must be a finite number from ${min} to ${max}`);
  return value;
}
function integer(name, value, min, max) {
  if (!bounded(value, min, max) || !Number.isInteger(value))
    throw new RangeError(`${name} must be a whole number from ${min} to ${max}`);
  return value;
}
function choice(name, value, options) {
  if (typeof value !== "string" || !options.includes(value))
    throw new RangeError(`${name} must be one of ${options.join(", ")}`);
  return value;
}
function flag(name, value) {
  if (typeof value !== "boolean")
    throw new TypeError(`${name} must be boolean`);
  return value;
}
function brightness(name, value) {
  if (!bounded(value, 0, 100) || !Number.isInteger(value))
    throw new RangeError(`${name} must be a whole percent between 0 and 100`);
  return value;
}
function kelvin(name, value) {
  if (!bounded(value, 2700, 6500) || value % 100 !== 0)
    throw new RangeError(`${name} must be a multiple of 100 between 2700 and 6500 K`);
  return value;
}
function morseSpans(text) {
  const spans = [];
  let cursor = 0;
  text.toUpperCase().split(" ").filter((word) => word).forEach((word, wordIndex) => {
    if (wordIndex)
      cursor += 7;
    [...word].forEach((char, charIndex) => {
      if (charIndex)
        cursor += 3;
      [...MORSE_CODE[char]].forEach((symbol, symbolIndex) => {
        if (symbolIndex)
          cursor += 1;
        const length = symbol === "." ? 1 : 3;
        spans.push([cursor, cursor + length]);
        cursor += length;
      });
    });
  });
  return { spans, units: cursor };
}
function keyframes(value) {
  if (!Array.isArray(value) || value.length < 2 || value.length > 256)
    throw new RangeError("keyframes must be a list of 2 to 256 frames");
  const frames = [];
  value.forEach((item, index) => {
    const name = `keyframes[${index}]`;
    if (!record(item))
      throw new TypeError(`${name} must be an object`);
    if (Object.keys(item).some((key) => !["at", "brightness", "cct", "ease"].includes(key)))
      throw new TypeError(`${name} accepts only at, brightness, cct and ease`);
    if (item.at === undefined)
      throw new TypeError(`${name}.at is required`);
    const at = number(`${name}.at`, item.at, 0, 3600);
    if (index === 0 && at !== 0)
      throw new RangeError("The first keyframe must be at 0 seconds");
    if (index && at - frames[index - 1].at < EFFECT_MIN_STEP - TOLERANCE)
      throw new RangeError(`${name}.at must be at least 0.1 s after the previous frame`);
    frames.push({
      at,
      brightness: item.brightness === undefined ? null : brightness(`${name}.brightness`, item.brightness),
      cct: item.cct === undefined ? null : kelvin(`${name}.cct`, item.cct),
      ease: item.ease === undefined ? "linear" : choice(`${name}.ease`, item.ease, ["hold", "linear", "smooth", "perceptual"])
    });
  });
  return frames;
}
function morseText(char) {
  return char === " " || char >= "a" && char <= "z" || Object.prototype.hasOwnProperty.call(MORSE_CODE, char);
}
function options(kind, data, period) {
  const get = (key, fallback) => data[key] === undefined ? fallback : data[key];
  switch (kind) {
    case "blink": {
      const duty = number("duty", get("duty", 0.5), 0, 1);
      if (duty * period < EFFECT_MIN_STEP - TOLERANCE || (1 - duty) * period < EFFECT_MIN_STEP - TOLERANCE) {
        throw new RangeError("Both blink phases (duty × period) must last at least 0.1 s");
      }
      return { pattern: kind, duty };
    }
    case "pulse": {
      const style = choice("style", get("style", "heartbeat"), ["heartbeat", "beat"]);
      const bpm = number("bpm", get("bpm", 60), 20, 180);
      if (style === "heartbeat" && bpm > 120)
        throw new RangeError("Heartbeat pulses require bpm of 120 or less");
      return {
        pattern: kind,
        bpm,
        style,
        accentEvery: integer("accentEvery", get("accentEvery", 0), 0, 16),
        accentRatio: number("accentRatio", get("accentRatio", 0.6), 0.2, 1),
        decay: number("decay", get("decay", 0.3), 0.05, 1)
      };
    }
    case "flicker": {
      const style = choice("style", get("style", "candle"), ["candle", "fire"]);
      const fire = style === "fire";
      return {
        pattern: kind,
        style,
        depth: number("depth", get("depth", fire ? 0.55 : 0.25), 0, 0.6),
        speed: number("speed", get("speed", 1), 0.25, 2),
        gusts: number("gusts", get("gusts", fire ? 0.5 : 0.15), 0, 1),
        cctCoupling: number("cctCoupling", get("cctCoupling", fire ? 0.5 : 0), 0, 1)
      };
    }
    case "sunrise":
      return { pattern: kind, direction: choice("direction", get("direction", "rise"), ["rise", "set"]) };
    case "lightning":
      return { pattern: kind, interval: number("interval", get("interval", 6), 2, 60), flashesMax: integer("flashesMax", get("flashesMax", 3), 1, 4) };
    case "emergency":
      return {
        pattern: kind,
        flashes: integer("flashes", get("flashes", 2), 1, 4),
        flashOn: number("flashOn", get("flashOn", 0.15), 0.1, 10),
        flashOff: number("flashOff", get("flashOff", 0.15), 0.1, 10),
        gap: number("gap", get("gap", 0.3), 0.2, 30)
      };
    case "paparazzi":
      return { pattern: kind, interval: number("interval", get("interval", 0.8), 0.33, 5), burst: number("burst", get("burst", 0.25), 0, 1) };
    case "tv":
      return { pattern: kind, interval: number("interval", get("interval", 2.5), 0.5, 10), motion: number("motion", get("motion", 0.3), 0, 1) };
    case "morse": {
      const text = get("text", "SOS");
      if (typeof text !== "string" || text.length < 1 || text.length > 64 || ![...text].every(morseText) || !text.trim()) {
        throw new RangeError("text must be 1 to 64 characters of A-Z, 0-9, space and . , ? / = + - @");
      }
      return { pattern: kind, text, unit: number("unit", get("unit", 0.2), 0.1, 2), loop: flag("loop", get("loop", true)) };
    }
    case "keyframes":
      if (data.keyframes === undefined)
        throw new TypeError("keyframes is required");
      return { pattern: kind, keyframes: keyframes(data.keyframes), loop: flag("loop", get("loop", true)) };
    default:
      return { pattern: kind };
  }
}
function parseEffectConfig(config) {
  if (!record(config))
    throw new TypeError("Only documented effect fields are supported");
  const data = config;
  if (!pattern(data.pattern))
    throw new TypeError("Unsupported effect pattern");
  const kind = data.pattern;
  const unknown = Object.keys(data).filter((key) => !COMMON_FIELDS.includes(key) && !PATTERN_FIELDS[kind].includes(key));
  if (unknown.length)
    throw new TypeError(`Only documented effect fields are supported; ${kind} does not accept ${unknown.sort().join(", ")}`);
  const period = data.period === undefined ? 4 : data.period;
  const [shortest, longest] = kind === "blink" ? [0.2, 60] : [1, 60];
  if (!bounded(period, shortest, longest))
    throw new RangeError(`Effect period must be ${shortest}–${longest} seconds`);
  const parsed = options(kind, data, period);
  const loops = parsed.pattern === "morse" || parsed.pattern === "keyframes" ? parsed.loop : !["fade-in", "fade-out", "sunrise"].includes(kind);
  const natural = parsed.pattern === "morse" && !parsed.loop ? morseSpans(parsed.text).units * parsed.unit : parsed.pattern === "keyframes" && !parsed.loop ? parsed.keyframes[parsed.keyframes.length - 1].at : null;
  let duration;
  if (data.duration === undefined)
    duration = natural ?? (kind === "sunrise" ? 900 : 10);
  else if (data.duration === null) {
    if (!loops)
      throw new RangeError(`Effect duration must be 1–3600 seconds; ${kind} requires a finite duration unless it loops`);
    duration = null;
  } else {
    if (!bounded(data.duration, 1, 3600))
      throw new RangeError("Effect duration must be 1–3600 seconds, or null for looping patterns");
    duration = natural === null ? data.duration : Math.min(data.duration, natural);
  }
  const restoreOnComplete = data.restoreOnComplete === undefined ? loops : flag("restoreOnComplete", data.restoreOnComplete);
  const minBrightness = data.minBrightness === undefined ? 0 : brightness("Effect brightness", data.minBrightness);
  const maxBrightness = data.maxBrightness === undefined ? null : brightness("Effect brightness", data.maxBrightness);
  if (maxBrightness !== null && maxBrightness < minBrightness)
    throw new RangeError("Effect maximum brightness must be at least the minimum");
  const [cct, cctEnd] = CCT_DEFAULTS[kind] ?? [null, 6500];
  return {
    ...parsed,
    duration,
    period,
    minBrightness,
    maxBrightness,
    restoreOnComplete,
    cct: data.cct === undefined ? cct : kelvin("Effect CCT", data.cct),
    cctEnd: data.cctEnd === undefined ? cctEnd : kelvin("Effect CCT", data.cctEnd),
    seed: data.seed === undefined ? 1 : integer("seed", data.seed, 0, SEED_MAX),
    curve: choice("curve", data.curve === undefined ? kind === "sunrise" ? "perceptual" : "linear" : data.curve, ["linear", "perceptual"])
  };
}
function validateEffectConfig(config) {
  parseEffectConfig(config);
}
function timestamp(value) {
  if (typeof value !== "string")
    return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!parts)
    return false;
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = parts;
  const y = Number(year), m = Number(month), d = Number(day);
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1] ?? 0;
  return m >= 1 && m <= 12 && d >= 1 && d <= days && Number(hour) <= 23 && Number(minute) <= 59 && Number(second) <= 59 && (offsetHour === undefined || Number(offsetHour) <= 23 && Number(offsetMinute) <= 59) && Number.isFinite(Date.parse(value));
}
function parseEffectStatus(value) {
  if (!record(value) || typeof value.running !== "boolean" || !(value.id === null || typeof value.id === "string" && value.id.trim().length > 0) || !(value.pattern === null || pattern(value.pattern)) || !(value.startedAt === null || timestamp(value.startedAt)) || !(value.endsAt === null || timestamp(value.endsAt)) || !(value.progress === null || bounded(value.progress, 0, 1)) || !(value.lastError === null || typeof value.lastError === "string") || value.running && (value.id === null || value.pattern === null || value.startedAt === null) || value.endsAt !== null && (value.startedAt === null || Date.parse(value.endsAt) < Date.parse(value.startedAt)) || value.running && value.endsAt === null !== (value.progress === null))
    throw new ProtocolError("Invalid bridge EffectStatus");
  return {
    running: value.running,
    id: value.id,
    pattern: value.pattern,
    startedAt: value.startedAt,
    endsAt: value.endsAt,
    progress: value.progress,
    lastError: value.lastError
  };
}

// src/bridge.ts
class BridgeClient {
  baseUrl;
  token;
  timeoutMs;
  fetcher;
  active = new Set;
  mutations = new SerialQueue;
  disposed = false;
  constructor(options = {}) {
    const url = new URL(options.baseUrl ?? "http://127.0.0.1:8877");
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
      throw new TypeError("Bridge URL must be HTTP(S), without credentials, query, or fragment");
    const token = options.token ?? "";
    if (typeof token !== "string" || /[\r\n]/.test(token))
      throw new TypeError("Bridge token must be a string without line breaks");
    this.baseUrl = url.href.replace(/\/$/, "");
    this.token = token.trim() ? token : "";
    this.timeoutMs = options.timeoutMs ?? 15000;
    validateTimeout(this.timeoutMs);
    const injected = options.fetch;
    const native = globalThis.fetch;
    if (injected)
      this.fetcher = (input, init) => injected(input, init);
    else if (typeof native === "function")
      this.fetcher = native.bind(globalThis);
    else
      throw new LighttimeError("unsupported", "A fetch implementation is required");
  }
  dispose() {
    this.disposed = true;
    for (const controller of this.active)
      controller.abort();
    this.active.clear();
  }
  async health(options = {}) {
    const value = await this.json("/v1/health", "GET", undefined, options);
    if (!record2(value) || typeof value.name !== "string" || typeof value.version !== "string" || value.authRequired !== undefined && typeof value.authRequired !== "boolean" || value.defaultAddress !== undefined && !(value.defaultAddress === null || typeof value.defaultAddress === "string" && value.defaultAddress.trim()))
      throw new ProtocolError("Invalid health response");
    return {
      name: value.name,
      version: value.version,
      ...value.authRequired === undefined ? {} : { authRequired: value.authRequired },
      ...value.defaultAddress === undefined ? {} : { defaultAddress: value.defaultAddress }
    };
  }
  async devices(timeout = 5, options = {}) {
    if (!Number.isFinite(timeout) || timeout < 0.1 || timeout > 30)
      throw new RangeError("Scan timeout must be 0.1–30 seconds");
    const value = await this.json(`/v1/devices?timeout=${encodeURIComponent(timeout)}`, "GET", undefined, { ...options, timeoutMs: options.timeoutMs ?? Math.max(this.timeoutMs, timeout * 1000 + 5000) });
    if (!record2(value) || !Array.isArray(value.devices) || !value.devices.every(validDevice))
      throw new ProtocolError("Invalid devices response");
    return value.devices;
  }
  async connect(connection = {}, options = {}) {
    return this.mutation("/v1/connect", "POST", { ...connection }, { ...options, timeoutMs: options.timeoutMs ?? 60000 }, parseState);
  }
  async disconnect(options = {}) {
    return this.mutation("/v1/disconnect", "POST", {}, options, parseState);
  }
  async refresh(options = {}) {
    return parseState(await this.json("/v1/state", "GET", undefined, options));
  }
  async setState(patch, options = {}) {
    validatePatch(patch);
    return this.mutation("/v1/state", "PATCH", { ...patch }, options, parseState);
  }
  async effect(options = {}) {
    return parseEffectStatus(await this.json("/v1/effects", "GET", undefined, { ...options, timeoutMs: options.timeoutMs ?? 60000 }));
  }
  async startEffect(config, options = {}) {
    validateEffectConfig(config);
    return this.mutation("/v1/effects/start", "POST", { ...config }, { ...options, timeoutMs: options.timeoutMs ?? 60000 }, parseEffectStatus);
  }
  async stopEffect(stop = {}, options = {}) {
    if (typeof stop !== "object" || stop === null || Array.isArray(stop) || Object.keys(stop).some((key) => key !== "restore") || stop.restore !== undefined && typeof stop.restore !== "boolean")
      throw new TypeError("Effect stop accepts only a boolean restore option");
    return this.mutation("/v1/effects/stop", "POST", { ...stop }, { ...options, timeoutMs: options.timeoutMs ?? 60000 }, parseEffectStatus);
  }
  async mutation(path, method, body, options, parse) {
    const timeoutMs = options.timeoutMs ?? this.timeoutMs;
    validateTimeout(timeoutMs);
    const scope = this.scope(options.signal);
    const op = deadline({ signal: scope.controller.signal, timeoutMs });
    try {
      const queued = this.mutations.run(async () => {
        op.check();
        return parse(await this.json(path, method, body, { signal: op.signal, timeoutMs }));
      });
      return await op.run(queued);
    } finally {
      op.close();
      scope.close();
    }
  }
  scope(signal) {
    if (this.disposed)
      throw new CancelledError;
    const controller = new AbortController;
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted)
      abort();
    this.active.add(controller);
    return { controller, close: () => {
      signal?.removeEventListener("abort", abort);
      this.active.delete(controller);
    } };
  }
  async json(path, method, body, options) {
    validateTimeout(options.timeoutMs ?? this.timeoutMs);
    const scope = this.scope(options.signal);
    const op = deadline({ signal: scope.controller.signal, timeoutMs: options.timeoutMs ?? this.timeoutMs });
    try {
      op.check();
      const response = await op.run(this.fetcher(this.baseUrl + path, {
        method,
        headers: { ...this.token ? { Authorization: `Bearer ${this.token}` } : {}, Accept: "application/json", ...body === undefined ? {} : { "Content-Type": "application/json" } },
        ...body === undefined ? {} : { body: JSON.stringify(body) },
        signal: op.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error"
      }));
      if (!response.ok)
        throw await op.run(httpError(response));
      try {
        return await op.run(response.json());
      } catch (error) {
        if (error instanceof SyntaxError)
          throw new ProtocolError("Bridge returned invalid JSON");
        throw error;
      }
    } finally {
      op.close();
      scope.close();
    }
  }
  async* events(options = {}) {
    const scope = this.scope(options.signal);
    const idleMs = options.idleTimeoutMs ?? 45000;
    const validIdle = Number.isFinite(idleMs) && idleMs > 0 && idleMs <= 300000;
    if (!validIdle) {
      scope.close();
      throw new RangeError("idleTimeoutMs must be 1–300000");
    }
    let reader;
    try {
      const response = await this.streamStep(scope.controller, idleMs, (signal) => this.fetcher(this.baseUrl + "/v1/events", {
        headers: { ...this.token ? { Authorization: `Bearer ${this.token}` } : {}, Accept: "text/event-stream" },
        signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error"
      }));
      if (!response.ok)
        throw await this.streamStep(scope.controller, idleMs, () => httpError(response));
      if (!response.headers.get("content-type")?.toLowerCase().includes("text/event-stream") || !response.body)
        throw new ProtocolError("Bridge did not return an SSE stream");
      reader = response.body.getReader();
      const decoder = new TextDecoder;
      const parser = new SseParser;
      while (true) {
        const chunk = await this.streamStep(scope.controller, idleMs, () => reader.read());
        if (chunk.done)
          throw new LighttimeError("stream_closed", "Bridge event stream closed");
        for (const data of parser.push(decoder.decode(chunk.value, { stream: true }))) {
          if (scope.controller.signal.aborted)
            throw new CancelledError;
          let value;
          try {
            value = JSON.parse(data);
          } catch {
            throw new ProtocolError("Invalid state event JSON");
          }
          yield parseState(value);
        }
      }
    } finally {
      scope.controller.abort();
      if (reader) {
        reader.cancel().catch(() => {});
        reader.releaseLock();
      }
      scope.close();
    }
  }
  async streamStep(controller, ms, work) {
    const op = deadline({ signal: controller.signal, timeoutMs: ms });
    try {
      op.check();
      return await op.run(work(controller.signal));
    } catch (error) {
      if (error instanceof TimeoutError)
        controller.abort();
      throw error;
    } finally {
      op.close();
    }
  }
}
function record2(value) {
  return typeof value === "object" && value !== null;
}
function validDevice(value) {
  return record2(value) && ["ble", "serial"].includes(String(value.transport)) && typeof value.address === "string" && typeof value.name === "string" && (value.rssi === undefined || typeof value.rssi === "number") && (value.accessible === undefined || typeof value.accessible === "boolean");
}
function parseState(value) {
  if (!record2(value) || typeof value.connected !== "boolean" || !["ble", "serial", "mock", "usb+ble", null].includes(value.transport) || !(value.address === null || typeof value.address === "string") || !(value.updatedAt === null || typeof value.updatedAt === "string") || !(value.power === null || typeof value.power === "boolean") || !(value.brightness === null || typeof value.brightness === "number" && Number.isFinite(value.brightness) && value.brightness >= 0 && value.brightness <= 100) || !(value.cct === null || typeof value.cct === "number" && Number.isInteger(value.cct) && value.cct >= 2700 && value.cct <= 6500) || !(value.device === null || record2(value.device) && typeof value.device.model === "string" && typeof value.device.serial === "string" && typeof value.device.id === "number" && Number.isInteger(value.device.id) && value.device.id >= 0 && value.device.id <= 65535))
    throw new ProtocolError("Invalid bridge State");
  return value;
}
async function httpError(response) {
  let code = `http_${response.status}`;
  let message = `Bridge request failed (HTTP ${response.status})`;
  try {
    const body = await response.json();
    if (record2(body) && record2(body.error)) {
      if (typeof body.error.code === "string")
        code = body.error.code;
      if (typeof body.error.message === "string")
        message = body.error.message;
    }
  } catch {}
  return new BridgeError(code, message, response.status);
}

class SseParser {
  buffer = "";
  event = "";
  data = [];
  bytes = 0;
  push(chunk) {
    this.buffer += chunk;
    const output = [];
    while (true) {
      const at = this.buffer.search(/[\r\n]/);
      if (at < 0)
        break;
      if (this.buffer[at] === "\r" && at === this.buffer.length - 1)
        break;
      const line = this.buffer.slice(0, at);
      const consumed = this.buffer.slice(at, at + 2) === `\r
` ? 2 : 1;
      this.buffer = this.buffer.slice(at + consumed);
      this.bytes += line.length;
      if (this.bytes > 65536)
        throw new ProtocolError("SSE event exceeds maximum size");
      if (line === "") {
        if (this.event === "state" && this.data.length)
          output.push(this.data.join(`
`));
        this.event = "";
        this.data = [];
        this.bytes = 0;
      } else if (!line.startsWith(":")) {
        const colon = line.indexOf(":");
        const key = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? "" : line.slice(colon + 1);
        if (value.startsWith(" "))
          value = value.slice(1);
        if (key === "event")
          this.event = value;
        else if (key === "data")
          this.data.push(value);
      }
    }
    if (this.buffer.length + this.bytes > 65536)
      throw new ProtocolError("SSE event exceeds maximum size");
    return output;
  }
}
// src/presets.ts
function freeze(value) {
  if (typeof value === "object" && value !== null && !Object.isFrozen(value)) {
    for (const item of Object.values(value))
      freeze(item);
    Object.freeze(value);
  }
  return value;
}
var EFFECT_PRESETS = freeze({
  candle: { pattern: "flicker", style: "candle", depth: 0.4, minBrightness: 2, maxBrightness: 16, duration: null },
  fireplace: { pattern: "flicker", style: "fire", minBrightness: 2, maxBrightness: 18, duration: null },
  storm: { pattern: "lightning", interval: 6, minBrightness: 0, maxBrightness: 20, duration: null },
  heartbeat: { pattern: "pulse", style: "heartbeat", bpm: 60, minBrightness: 2, maxBrightness: 16, duration: null },
  sunrise15: { pattern: "sunrise", direction: "rise", duration: 900, minBrightness: 0, maxBrightness: 20 },
  sunset: { pattern: "sunrise", direction: "set", duration: 900, minBrightness: 0, maxBrightness: 20 },
  emergency: { pattern: "emergency", minBrightness: 0, maxBrightness: 20, duration: null },
  paparazzi: { pattern: "paparazzi", interval: 0.8, burst: 0.25, minBrightness: 0, maxBrightness: 20, duration: null },
  television: { pattern: "tv", interval: 2.5, motion: 0.3, minBrightness: 2, maxBrightness: 16, duration: null },
  sos: { pattern: "morse", text: "SOS", unit: 0.2, minBrightness: 0, maxBrightness: 16, duration: null },
  keyframes: { pattern: "keyframes", duration: null, keyframes: [
    { at: 0, brightness: 4, cct: 2700 },
    { at: 2, brightness: 16, cct: 4300, ease: "smooth" },
    { at: 3.5, cct: 6500, ease: "linear" },
    { at: 4, brightness: 8, ease: "hold" },
    { at: 6, brightness: 4, cct: 2700, ease: "perceptual" }
  ] }
});
// src/pattern-engine.ts
var SAMPLE_INTERVAL = 0.1;
function quantizeBrightness(value) {
  return Math.min(100, Math.max(0, Math.floor(value + 0.5)));
}
function quantizeCct(value) {
  return Math.min(6500, Math.max(2700, 100 * Math.floor(value / 100 + 0.5)));
}
function effectHash32(value) {
  let x = value >>> 0;
  x ^= x >>> 16;
  x = Math.imul(x, 2146121005);
  x ^= x >>> 15;
  x = Math.imul(x, 2221713035);
  x ^= x >>> 16;
  return x >>> 0;
}
function effectRandom(seed, stream, index) {
  return effectHash32(seed ^ effectHash32(Math.imul(stream, 2654435769) + index >>> 0)) / 4294967296;
}
var smooth = (u) => u * u * (3 - 2 * u);
var perceptual = (u) => u ** 2.2;
var cosine = (u) => (1 - Math.cos(Math.PI * u)) / 2;
var expGap = (mean, u, low, high) => Math.min(high, Math.max(low, -mean * Math.log(1 - u)));
function noise(seed, stream, t, period) {
  const x = t / period, n = Math.floor(x), a = effectRandom(seed, stream, n);
  return a + (effectRandom(seed, stream, n + 1) - a) * cosine(x - n);
}
function firstAfter(t, candidates) {
  let best = null;
  for (const candidate of candidates)
    if (candidate > t && (best === null || candidate < best))
      best = candidate;
  return best;
}
function bisectRight(values, value) {
  let low = 0, high = values.length;
  while (low < high) {
    const mid = low + high >>> 1;
    if (value < values[mid])
      high = mid;
    else
      low = mid + 1;
  }
  return low;
}

class Renewal {
  gap;
  anchored;
  index = 0;
  previous = null;
  start = 0;
  following = 0;
  constructor(gap, anchored = false) {
    this.gap = gap;
    this.anchored = anchored;
    this.rewind();
  }
  rewind() {
    this.index = 0;
    this.previous = null;
    this.start = this.anchored ? 0 : this.gap(0);
    this.following = this.start + this.gap(1);
  }
  locate(t) {
    if (t < this.start) {
      this.rewind();
      if (t < this.start)
        return { index: -1, previous: null, start: null, following: this.start };
    }
    while (this.following <= t) {
      ++this.index;
      this.previous = this.start;
      this.start = this.following;
      this.following = this.start + this.gap(this.index + 1);
    }
    return { index: this.index, previous: this.previous, start: this.start, following: this.following };
  }
}

class Levels {
  low;
  high;
  cct;
  cctEnd;
  seed;
  curve;
  constructor(low, high, cct, cctEnd, seed, curve) {
    this.low = low;
    this.high = high;
    this.cct = cct;
    this.cctEnd = cctEnd;
    this.seed = seed;
    this.curve = curve;
  }
  at(u) {
    return this.low + (this.high - this.low) * u;
  }
  target(brightness, cct = this.cct) {
    return { brightness: quantizeBrightness(brightness), cct: quantizeCct(cct) };
  }
}

class Program {
  nextChange(t) {
    return t + SAMPLE_INTERVAL;
  }
}

class Blink extends Program {
  levels;
  period;
  duty;
  constructor(levels, period, duty) {
    super();
    this.levels = levels;
    this.period = period;
    this.duty = duty;
  }
  sample(t) {
    const phase = t / this.period % 1;
    return this.levels.target(phase < this.duty ? this.levels.high : this.levels.low);
  }
  nextChange(t) {
    const start = Math.floor(t / this.period) * this.period;
    return firstAfter(t, [
      start + this.duty * this.period,
      start + this.period,
      start + this.period + this.duty * this.period,
      start + 2 * this.period
    ]);
  }
}

class Breathe extends Program {
  levels;
  period;
  constructor(levels, period) {
    super();
    this.levels = levels;
    this.period = period;
  }
  sample(t) {
    const phase = t / this.period % 1, u = (1 - Math.cos(2 * Math.PI * phase)) / 2;
    return this.levels.target(this.levels.at(this.levels.curve(u)));
  }
}

class Fade extends Program {
  levels;
  duration;
  rising;
  constructor(levels, duration, rising) {
    super();
    this.levels = levels;
    this.duration = duration;
    this.rising = rising;
  }
  sample(t) {
    let progress = Math.min(1, Math.max(0, t / this.duration));
    if (!this.rising)
      progress = 1 - progress;
    return this.levels.target(this.levels.at(this.levels.curve(progress)));
  }
  nextChange(t) {
    return t < this.duration ? t + SAMPLE_INTERVAL : null;
  }
}

class CctSweep extends Program {
  levels;
  period;
  constructor(levels, period) {
    super();
    this.levels = levels;
    this.period = period;
  }
  sample(t) {
    const phase = t / this.period % 1, span = this.levels.cctEnd - this.levels.cct;
    return this.levels.target(this.levels.high, this.levels.cct + span * (1 - Math.abs(2 * phase - 1)));
  }
}

class Pulse extends Program {
  levels;
  options;
  beat;
  second;
  constructor(levels, options) {
    super();
    this.levels = levels;
    this.options = options;
    this.beat = 60 / options.bpm;
    this.second = Math.min(0.35, Math.max(0.2, 0.33 * this.beat));
  }
  sample(t) {
    const options = this.options, k = Math.floor(t / this.beat), x = Math.max(0, t - k * this.beat);
    const accent = options.accentEvery === 0 || k % options.accentEvery === 0;
    const a = accent ? 1 : options.accentRatio;
    let envelope;
    if (options.style === "beat")
      envelope = a * Math.exp(-x / (options.decay * this.beat));
    else {
      const first = a * Math.exp(-x / 0.12);
      envelope = x < this.second ? first : Math.max(first, 0.65 * a * Math.exp(-(x - this.second) / 0.12));
    }
    return this.levels.target(this.levels.at(envelope));
  }
  nextChange(t) {
    const start = Math.floor(t / this.beat) * this.beat;
    const onsets = [start, start + this.beat, start + 2 * this.beat];
    if (this.options.style === "heartbeat")
      onsets.push(start + this.second, start + this.beat + this.second);
    const onset = firstAfter(t, onsets), grid = t + SAMPLE_INTERVAL;
    return onset === null ? grid : Math.min(grid, onset);
  }
}

class Flicker extends Program {
  levels;
  options;
  h1;
  gusts;
  constructor(levels, options) {
    super();
    this.levels = levels;
    this.options = options;
    const candle = options.style === "candle", mean = candle ? 8 : 3;
    this.h1 = Math.max(0.2, (candle ? 0.45 : 0.3) / options.speed);
    this.gusts = new Renewal((i) => expGap(mean, effectRandom(levels.seed, 3, i), 0.5, 30));
  }
  gust(t) {
    const located = this.gusts.locate(t);
    let strength = 0;
    const events = [[located.index, located.start], [located.index - 1, located.previous]];
    for (const [index, start] of events) {
      if (index < 0 || start === null)
        continue;
      const length = 0.3 + 0.5 * effectRandom(this.levels.seed, 4, index);
      if (start <= t && t < start + length)
        strength = Math.max(strength, Math.sin(Math.PI * (t - start) / length) ** 2);
    }
    return strength;
  }
  sample(t) {
    const levels = this.levels, options = this.options;
    const n = 0.6 * noise(levels.seed, 1, t, this.h1) + 0.4 * noise(levels.seed, 2, t, 0.2);
    let dip = options.depth * n;
    if (options.gusts)
      dip += 0.35 * options.gusts * this.gust(t);
    dip = Math.min(1, Math.max(0, dip));
    const warmth = options.cctCoupling * (1 - noise(levels.seed, 5, t, 1.5));
    return levels.target(levels.high - (levels.high - levels.low) * dip, levels.cct + (levels.cctEnd - levels.cct) * warmth);
  }
}

class Sunrise extends Program {
  levels;
  duration;
  rising;
  constructor(levels, duration, rising) {
    super();
    this.levels = levels;
    this.duration = duration;
    this.rising = rising;
  }
  sample(t) {
    const u = Math.min(1, Math.max(0, t / this.duration)), w = this.rising ? u : 1 - u;
    return this.levels.target(this.levels.at(this.levels.curve(w)), this.levels.cct + (this.levels.cctEnd - this.levels.cct) * smooth(w));
  }
  nextChange(t) {
    return t < this.duration ? t + SAMPLE_INTERVAL : null;
  }
}

class Lightning extends Program {
  levels;
  options;
  strikes;
  constructor(levels, options) {
    super();
    this.levels = levels;
    this.options = options;
    this.strikes = new Renewal((i) => expGap(options.interval, effectRandom(levels.seed, 1, i), 1.5, 4 * options.interval));
  }
  segments(index, start) {
    const levels = this.levels, count = 1 + Math.floor(this.options.flashesMax * effectRandom(levels.seed, 2, index));
    const segments = [];
    let begin = start;
    for (let flash = 0;flash < count; ++flash) {
      const key = 16 * index + flash, end = begin + 0.1;
      segments.push([begin, end, levels.at(0.6 + 0.4 * effectRandom(levels.seed, 3, key))]);
      begin = end;
      if (flash < count - 1)
        begin = end + (0.1 + 0.15 * effectRandom(levels.seed, 4, key));
    }
    segments.push([begin, begin + 0.1, levels.at(0.25)]);
    return segments;
  }
  sample(t) {
    const located = this.strikes.locate(t);
    let level = this.levels.low;
    if (located.start !== null)
      for (const [begin, end, value] of this.segments(located.index, located.start)) {
        if (begin <= t && t < end) {
          level = value;
          break;
        }
      }
    return this.levels.target(level);
  }
  nextChange(t) {
    const located = this.strikes.locate(t), edges = [located.following];
    if (located.start !== null)
      for (const [begin, end] of this.segments(located.index, located.start))
        edges.push(begin, end);
    return firstAfter(t, edges);
  }
}

class Emergency extends Program {
  levels;
  options;
  cycle;
  half;
  period;
  switchAt;
  edges;
  constructor(levels, options) {
    super();
    this.levels = levels;
    this.options = options;
    this.cycle = options.flashOn + options.flashOff;
    this.half = options.flashes * this.cycle + options.gap;
    this.period = 2 * this.half;
    this.switchAt = (options.flashes - 1) * this.cycle + options.flashOn + 0.1;
    const edges = new Set([this.switchAt]);
    for (let flash = 0;flash < options.flashes; ++flash) {
      edges.add(flash * this.cycle);
      edges.add(flash * this.cycle + options.flashOn);
    }
    this.edges = [...new Set([...edges, ...[...edges].map((edge) => this.half + edge), this.period])].sort((a, b) => a - b);
  }
  sample(t) {
    const u = t % this.period, half = u >= this.half ? 1 : 0, v = u - half * this.half;
    const flash = Math.floor(v / this.cycle), lit = flash < this.options.flashes && v - flash * this.cycle < this.options.flashOn;
    const side = (half + (v >= this.switchAt ? 1 : 0)) % 2;
    return this.levels.target(lit ? this.levels.high : this.levels.low, side === 0 ? this.levels.cct : this.levels.cctEnd);
  }
  nextChange(t) {
    const base = t - t % this.period;
    return firstAfter(t, [...this.edges.map((edge) => base + edge), base + this.period + this.edges[0]]);
  }
}

class Paparazzi extends Program {
  levels;
  options;
  flashes;
  constructor(levels, options) {
    super();
    this.levels = levels;
    this.options = options;
    this.flashes = new Renewal((i) => expGap(options.interval, effectRandom(levels.seed, 1, i), 0.2, 6 * options.interval));
  }
  spans(index, start) {
    const level = this.levels.at(0.8 + 0.2 * effectRandom(this.levels.seed, 2, index));
    const spans = [[start, start + 0.1, level]];
    if (effectRandom(this.levels.seed, 3, index) < this.options.burst)
      spans.push([start + 0.2, start + 0.3, level]);
    return spans;
  }
  active(t) {
    const located = this.flashes.locate(t), spans = [];
    const events = [[located.index, located.start], [located.index - 1, located.previous]];
    for (const [index, start] of events)
      if (index >= 0 && start !== null)
        spans.push(...this.spans(index, start));
    return spans;
  }
  sample(t) {
    let level = this.levels.low;
    for (const [begin, end, value] of this.active(t))
      if (begin <= t && t < end)
        level = Math.max(level, value);
    return this.levels.target(level);
  }
  nextChange(t) {
    const edges = [this.flashes.locate(t).following];
    for (const [begin, end] of this.active(t))
      edges.push(begin, end);
    return firstAfter(t, edges);
  }
}

class Television extends Program {
  levels;
  options;
  cuts;
  constructor(levels, options) {
    super();
    this.levels = levels;
    this.options = options;
    this.cuts = new Renewal((i) => expGap(options.interval, effectRandom(levels.seed, 1, i), 0.4, 4 * options.interval), true);
  }
  sample(t) {
    const levels = this.levels, motion = this.options.motion, scene = this.cuts.locate(t).index;
    let level = levels.at(0.25 + 0.75 * effectRandom(levels.seed, 2, scene));
    if (motion)
      level *= 1 + 0.15 * motion * (2 * noise(levels.seed, 4, t, 0.25) - 1);
    const cct = levels.cct + (levels.cctEnd - levels.cct) * effectRandom(levels.seed, 3, scene);
    return levels.target(Math.min(levels.high, Math.max(levels.low, level)), cct);
  }
  nextChange(t) {
    const following = this.cuts.locate(t).following;
    return this.options.motion ? Math.min(t + SAMPLE_INTERVAL, following) : following;
  }
}

class Morse extends Program {
  levels;
  options;
  starts;
  ends;
  edges;
  period;
  constructor(levels, options) {
    super();
    this.levels = levels;
    this.options = options;
    const { spans, units } = morseSpans(options.text);
    this.starts = spans.map(([start]) => start * options.unit);
    this.ends = spans.map(([, end]) => end * options.unit);
    this.edges = [...this.starts, ...this.ends].sort((a, b) => a - b);
    this.period = (units + 7) * options.unit;
  }
  sample(t, final = false) {
    let u = t;
    if (this.options.loop) {
      u = t % this.period;
      if (final && t > 0 && u === 0)
        u = this.period;
    }
    const index = bisectRight(this.starts, u) - 1, lit = index >= 0 && u < this.ends[index];
    return this.levels.target(lit ? this.levels.high : this.levels.low);
  }
  nextChange(t) {
    if (!this.options.loop)
      return this.edges[bisectRight(this.edges, t)] ?? null;
    const base = t - t % this.period;
    return firstAfter(t, [...this.edges.map((edge) => base + edge), base + this.period, base + 2 * this.period]);
  }
}

class Keyframes extends Program {
  options;
  times = [];
  values = [];
  period;
  constructor(options, baseline) {
    super();
    this.options = options;
    let { brightness, cct } = baseline;
    for (const frame of options.keyframes) {
      brightness = frame.brightness ?? brightness;
      cct = frame.cct ?? cct;
      this.times.push(frame.at);
      this.values.push({ brightness, cct });
    }
    this.period = this.times[this.times.length - 1];
  }
  sample(t, final = false) {
    let u = Math.min(t, this.period);
    if (this.options.loop) {
      u = t % this.period;
      if (final && t > 0 && u === 0)
        u = this.period;
    }
    const index = bisectRight(this.times, u);
    let brightness, cct;
    if (index >= this.times.length)
      ({ brightness, cct } = this.values[this.values.length - 1]);
    else {
      const previous = this.values[index - 1], next = this.values[index], ease = this.options.keyframes[index].ease;
      ({ brightness, cct } = previous);
      if (ease !== "hold") {
        const start = this.times[index - 1], progress = (u - start) / (this.times[index] - start);
        const f = ease === "smooth" ? smooth(progress) : ease === "perceptual" ? perceptual(progress) : progress;
        brightness += (next.brightness - brightness) * f;
        cct += (next.cct - cct) * f;
      }
    }
    return { brightness: quantizeBrightness(brightness), cct: quantizeCct(cct) };
  }
  nextChange(t) {
    if (!this.options.loop && t >= this.period)
      return null;
    const u = this.options.loop ? t % this.period : t;
    const index = Math.min(bisectRight(this.times, u), this.times.length - 1);
    const boundary = t - u + this.times[index];
    if (boundary <= t)
      return t + SAMPLE_INTERVAL;
    const previous = this.values[index - 1], next = this.values[index];
    if (this.options.keyframes[index].ease === "hold" || previous.brightness === next.brightness && previous.cct === next.cct)
      return boundary;
    return Math.min(t + SAMPLE_INTERVAL, boundary);
  }
}
function build(config, levels, baseline) {
  switch (config.pattern) {
    case "blink":
      return new Blink(levels, config.period, config.duty);
    case "breathe":
      return new Breathe(levels, config.period);
    case "fade-in":
    case "fade-out":
      return new Fade(levels, config.duration, config.pattern === "fade-in");
    case "cct-sweep":
      return new CctSweep(levels, config.period);
    case "pulse":
      return new Pulse(levels, config);
    case "flicker":
      return new Flicker(levels, config);
    case "sunrise":
      return new Sunrise(levels, config.duration, config.direction === "rise");
    case "lightning":
      return new Lightning(levels, config);
    case "emergency":
      return new Emergency(levels, config);
    case "paparazzi":
      return new Paparazzi(levels, config);
    case "tv":
      return new Television(levels, config);
    case "morse":
      return new Morse(levels, config);
    case "keyframes":
      return new Keyframes(config, baseline);
  }
}
function resolveEffect(config, baseline) {
  const parsed = parseEffectConfig(config), { brightness, cct } = baseline;
  if (brightness === null || !Number.isInteger(brightness) || brightness < 0 || brightness > 100) {
    throw new RangeError("Effect baseline brightness must be a whole percent between 0 and 100");
  }
  if (cct === null || !Number.isInteger(cct) || cct % 100 !== 0 || cct < 2700 || cct > 6500) {
    throw new RangeError("Effect baseline CCT must be a multiple of 100 between 2700 and 6500 K");
  }
  const maximum = parsed.maxBrightness ?? (brightness || 5);
  if (parsed.minBrightness > maximum)
    throw new RangeError("maxBrightness must be >= minBrightness (including its baseline default)");
  const resolved = { ...parsed, maxBrightness: maximum, cct: parsed.cct ?? cct };
  const levels = new Levels(resolved.minBrightness, maximum, resolved.cct, resolved.cctEnd, resolved.seed, resolved.curve === "perceptual" ? perceptual : (u) => u);
  const program = build(resolved, levels, { brightness, cct });
  const duration = resolved.duration;
  return {
    config: resolved,
    duration,
    restoreOnComplete: resolved.restoreOnComplete,
    target(elapsedSeconds) {
      const final = duration !== null && elapsedSeconds >= duration;
      return program.sample(final ? duration : elapsedSeconds, final);
    },
    sample(tSeconds, final = false) {
      return program.sample(tSeconds, final);
    },
    nextChange(elapsedSeconds) {
      return program.nextChange(elapsedSeconds);
    }
  };
}
// src/direct-effects.ts
var WRITE_SPACING_MS = 100;
var IDLE_RESAMPLE_SECONDS = 1;
var nextId = 0;

class DirectEffectRunner {
  controller;
  queue = new SerialQueue;
  listeners = new Set;
  loops = new Set;
  calls = new Set;
  now;
  wallNow;
  sleep;
  unsubscribe;
  generation = 0;
  command = null;
  run = null;
  lastError = null;
  nextWriteAt = -Infinity;
  closing = false;
  disposed = false;
  disposing = null;
  constructor(controller, options = {}) {
    this.controller = controller;
    this.now = options.now ?? (() => performance.now());
    this.wallNow = options.wallNow ?? Date.now;
    this.sleep = options.sleep ?? pause;
    this.unsubscribe = controller.subscribe((state) => {
      if (!state.connected)
        this.cancel();
    });
  }
  get status() {
    const run = this.run;
    return {
      running: run?.running ?? false,
      id: run?.id ?? null,
      pattern: run?.effect.config.pattern ?? null,
      startedAt: run ? new Date(run.startedAt).toISOString() : null,
      endsAt: run && run.effect.duration !== null ? new Date(run.startedAt + run.effect.duration * 1000).toISOString() : null,
      progress: run && run.effect.duration !== null ? Math.min(1, this.elapsed(run) / run.effect.duration) : null,
      lastError: this.lastError
    };
  }
  subscribe(listener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  publish() {
    for (const listener of this.listeners) {
      try {
        listener(this.status);
      } catch {}
    }
  }
  elapsed(run) {
    return run.running ? Math.max(0, (this.now() - run.started) / 1000) : run.elapsed;
  }
  cancel() {
    const previous = this.command;
    ++this.generation;
    this.command = null;
    if (this.run?.running) {
      this.run.elapsed = this.elapsed(this.run);
      this.run.running = false;
    }
    previous?.abort.abort();
    for (const call of [...this.calls])
      call.abort();
    this.publish();
  }
  begin() {
    if (this.closing)
      throw new CancelledError;
    const previous = this.command;
    if (this.run?.running) {
      this.run.elapsed = this.elapsed(this.run);
      this.run.running = false;
    }
    const command = { generation: ++this.generation, abort: new AbortController };
    this.command = command;
    previous?.abort.abort();
    this.publish();
    return command;
  }
  valid(command) {
    return !this.closing && command.generation === this.generation && !command.abort.signal.aborted;
  }
  check(command) {
    if (!this.valid(command))
      throw new CancelledError;
  }
  fail(command, error) {
    if (!this.valid(command))
      return;
    if (this.run?.running) {
      this.run.elapsed = this.elapsed(this.run);
      this.run.running = false;
    }
    this.lastError = error instanceof Error ? error.message : String(error);
    command.abort.abort();
    this.publish();
  }
  enqueue(command, work) {
    return this.queue.run(async () => {
      this.check(command);
      await this.controller.drain();
      this.check(command);
      return work();
    });
  }
  control(command, work) {
    return this.enqueue(command, work).catch((error) => {
      if (!this.valid(command))
        throw new CancelledError;
      this.fail(command, error);
      throw error;
    });
  }
  async waitForOutput(command) {
    while (true) {
      this.check(command);
      const remaining = this.nextWriteAt - this.now();
      if (remaining <= 0)
        return;
      await this.sleep(remaining, command.abort.signal);
    }
  }
  async call(command, work) {
    this.check(command);
    const call = new AbortController;
    this.calls.add(call);
    try {
      const actual = await work(call.signal);
      this.check(command);
      return actual;
    } finally {
      this.calls.delete(call);
    }
  }
  async write(command, patch) {
    this.check(command);
    this.nextWriteAt = this.now() + WRITE_SPACING_MS;
    return this.call(command, (signal) => this.controller.setState(patch, { signal }));
  }
  async start(config) {
    parseEffectConfig(config);
    const requested = config.pattern === "keyframes" ? { ...config, keyframes: config.keyframes.map((frame) => ({ ...frame })) } : { ...config };
    const command = this.begin();
    return this.control(command, async () => {
      const baseline = await this.call(command, (signal) => this.controller.refresh({ signal }));
      this.check(command);
      const effect = resolveEffect(requested, baseline);
      if (!baseline.connected || baseline.brightness === null || baseline.cct === null)
        throw new DisconnectedError;
      const run = {
        command,
        effect,
        baseline: { brightness: baseline.brightness, cct: baseline.cct },
        id: `effect-${this.wallNow()}-${++nextId}`,
        started: this.now(),
        startedAt: this.wallNow(),
        running: true,
        elapsed: 0
      };
      this.run = run;
      this.lastError = null;
      this.publish();
      this.check(command);
      const loop = this.runLoop(run);
      this.loops.add(loop);
      loop.then(() => {
        this.loops.delete(loop);
      });
      return this.status;
    });
  }
  async stop(options = {}) {
    if (options === null || typeof options !== "object" || Array.isArray(options) || Object.keys(options).some((key) => key !== "restore") || options.restore !== undefined && typeof options.restore !== "boolean")
      throw new TypeError("Stop accepts only a boolean restore");
    const restore = options.restore ?? true;
    const active = this.run?.running ? this.run : null;
    const command = this.begin();
    return this.control(command, async () => {
      if (active && restore) {
        await this.waitForOutput(command);
        await this.write(command, active.baseline);
      }
      return this.status;
    });
  }
  async setState(patch) {
    validatePatch(patch);
    const requested = { ...patch };
    const command = this.begin();
    return this.control(command, async () => {
      await this.waitForOutput(command);
      return this.write(command, requested);
    });
  }
  async step(run) {
    return this.enqueue(run.command, async () => {
      await this.waitForOutput(run.command);
      this.check(run.command);
      const elapsed = this.elapsed(run);
      const target = run.effect.target(elapsed);
      const state = this.controller.state;
      if (!state.connected || state.brightness === null || state.cct === null)
        throw new DisconnectedError;
      const patch = {};
      if (Math.abs(state.brightness - target.brightness) > 0.01)
        patch.brightness = target.brightness;
      if (state.cct !== target.cct)
        patch.cct = target.cct;
      if (Object.keys(patch).length)
        await this.write(run.command, patch);
      this.check(run.command);
      const complete = run.effect.duration !== null && elapsed >= run.effect.duration;
      if (complete) {
        if (run.effect.restoreOnComplete) {
          await this.controller.drain();
          await this.waitForOutput(run.command);
          await this.write(run.command, run.baseline);
        }
        run.elapsed = run.effect.duration;
        run.running = false;
      }
      this.publish();
      return complete;
    });
  }
  async runLoop(run) {
    try {
      while (this.valid(run.command) && run.running) {
        if (await this.step(run))
          return;
        this.check(run.command);
        const elapsed = this.elapsed(run);
        if (run.effect.duration !== null && elapsed >= run.effect.duration)
          continue;
        const next = run.effect.nextChange(elapsed) ?? elapsed + IDLE_RESAMPLE_SECONDS;
        let wake = Math.min(elapsed + IDLE_RESAMPLE_SECONDS, Math.max(elapsed, next));
        if (run.effect.duration !== null)
          wake = Math.min(wake, run.effect.duration);
        await this.sleep(Math.max(1, run.started + wake * 1000 + 1 - this.now()), run.command.abort.signal);
      }
    } catch (error) {
      this.fail(run.command, error);
    }
  }
  dispose() {
    this.closing = true;
    this.cancel();
    if (this.disposed)
      return Promise.resolve();
    if (this.disposing)
      return this.disposing;
    const pending = (async () => {
      await this.controller.drain();
      await this.queue.drain();
      await Promise.all([...this.loops]);
      await this.controller.drain();
      this.disposed = true;
      this.unsubscribe();
      this.listeners.clear();
    })();
    this.disposing = pending;
    pending.catch((error) => {
      if (this.disposing === pending)
        this.disposing = null;
      this.lastError = error instanceof Error ? error.message : String(error);
      this.publish();
    });
    return pending;
  }
}
export {
  BleProtocolTransport,
  BridgeClient,
  BridgeError,
  COMMAND,
  CancelledError,
  DirectEffectRunner,
  DisconnectedError,
  EFFECT_MIN_STEP,
  EFFECT_PATTERNS,
  EFFECT_PRESETS,
  FrameParser,
  G60_NOTIFY,
  G60_SERVICE,
  G60_WRITE,
  LightController,
  LighttimeError,
  MORSE_CODE,
  ProtocolError,
  REQUEST_TYPE,
  RESPONSE_TYPE,
  TimeoutError,
  VerificationError,
  WebBluetoothTransport,
  crc16Xmodem,
  decodeValue,
  disconnectedState,
  effectHash32,
  effectRandom,
  encodeFrame,
  morseSpans,
  parseEffectConfig,
  parseEffectStatus,
  parseState,
  quantizeBrightness,
  quantizeCct,
  resolveEffect,
  validateEffectConfig,
  validatePatch,
  valuePayload
};
