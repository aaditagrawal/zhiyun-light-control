// Package self-reference resolves exports.import -> dist/index.js, never the TypeScript sources.
import { BridgeClient, COMMAND, FrameParser, encodeFrame, REQUEST_TYPE, LightController, BleProtocolTransport, WebBluetoothTransport } from '@lighttime/sdk';
const tx = encodeFrame({ messageType: REQUEST_TYPE, sequence: 1, command: COMMAND.info, payload: new Uint8Array() });
if (new FrameParser().push(tx)[0]?.command !== COMMAND.info) throw new Error('Public protocol exports failed');
for (const entry of [BridgeClient, LightController, BleProtocolTransport, WebBluetoothTransport]) {
  if (typeof entry !== 'function') throw new Error('Public class export missing');
}
console.log('Public ESM package import smoke passed.');
