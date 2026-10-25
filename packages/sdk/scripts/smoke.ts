// Package self-reference resolves exports.import -> dist/index.js, never the TypeScript sources.
import { BridgeClient, COMMAND, FrameParser, encodeFrame, REQUEST_TYPE, LightController, BleProtocolTransport, WebBluetoothTransport, DirectEffectRunner, EFFECT_PATTERNS, EFFECT_PRESETS, parseEffectConfig, resolveEffect } from '@lighttime/sdk';
const tx = encodeFrame({ messageType: REQUEST_TYPE, sequence: 1, command: COMMAND.info, payload: new Uint8Array() });
if (new FrameParser().push(tx)[0]?.command !== COMMAND.info) throw new Error('Public protocol exports failed');
for (const entry of [BridgeClient, LightController, BleProtocolTransport, WebBluetoothTransport, DirectEffectRunner]) {
  if (typeof entry !== 'function') throw new Error('Public class export missing');
}
if (EFFECT_PATTERNS.length !== 14 || Object.keys(EFFECT_PRESETS).length !== 11) throw new Error('Public effect catalog incomplete');
parseEffectConfig(EFFECT_PRESETS.candle!);
const effect = resolveEffect({ pattern: 'fade-in', duration: 2, maxBrightness: 12 }, { brightness: 7, cct: 5000 });
if (effect.target(2).brightness !== 12) throw new Error('Public effect engine failed');
console.log('Public ESM package import smoke passed.');
