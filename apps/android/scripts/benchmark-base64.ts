// Run with the Bun version pinned by package.json. Baseline installation and
// compilation happen before timing; all measured paths use in-memory fixtures.
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { cpus, loadavg, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { decodeBase64, encodeBase64 } from '../src/ble/base64';
import { NativeBleAdapter } from '../src/ble/NativeBleAdapter';
import { G60_NOTIFY, G60_SERVICE, G60_WRITE } from '../src/ble/constants';
import type { BleManager } from 'react-native-ble-plx';
import captures from '../test/fixtures/g60-reads.json';

const { values } = parseArgs({ options: {
  'baseline-ref': { type: 'string', default: 'b9325b22aaffb759d69bd6bfebf483b68190bb24' },
  samples: { type: 'string', default: '21' },
} });
const samples = Number(values.samples);
if (!Number.isInteger(samples) || samples < 5) throw new Error('Use at least five samples.');
const android = resolve(import.meta.dir, '..');
const repository = resolve(android, '../..');
const manifest = await Bun.file(join(android, 'package.json')).json();
if (`bun@${Bun.version}` !== manifest.packageManager) throw new Error(`Run with ${manifest.packageManager}.`);
function source(path: string): string {
  const result = Bun.spawnSync(['git', 'show', `${values['baseline-ref']}:${path}`], { cwd: repository });
  if (result.exitCode) throw new Error(result.stderr.toString());
  return result.stdout.toString();
}

const baselineManifest = JSON.parse(source('apps/android/package.json'));
const baselineLock = Bun.JSON5.parse(source('apps/android/bun.lock'));
const version = baselineManifest.dependencies['base64-js'];
if (version !== '1.5.1' || !baselineLock.packages['base64-js'][0].startsWith(`base64-js@${version}`)) {
  throw new Error('The selected baseline must consume base64-js 1.5.1.');
}
const temporary = await mkdtemp(join(tmpdir(), 'lighttime-codec-baseline-'));
let sink = 0;
try {
  // Use the committed integrity entry, not a freshly resolved approximation.
  const fixtureManifest = { name: 'lighttime-codec-baseline', dependencies: { 'base64-js': version } };
  await writeFile(join(temporary, 'package.json'), JSON.stringify(fixtureManifest));
  await writeFile(join(temporary, 'bun.lock'), JSON.stringify({
    lockfileVersion: 2, configVersion: 1,
    workspaces: { '': fixtureManifest }, packages: { 'base64-js': baselineLock.packages['base64-js'] },
  }));
  const install = Bun.spawnSync([process.execPath, 'install', '--frozen-lockfile', '--ignore-scripts'], { cwd: temporary });
  if (install.exitCode) throw new Error(install.stderr.toString());
  const baselineModule = join(temporary, 'node_modules/base64-js/index.js');
  const previous = await import(pathToFileURL(baselineModule).href);
  const adapterSource = source('apps/android/src/ble/NativeBleAdapter.ts')
    .replace("from 'base64-js'", `from ${JSON.stringify(baselineModule)}`);
  await writeFile(join(temporary, 'constants.ts'), source('apps/android/src/ble/constants.ts'));
  await writeFile(join(temporary, 'adapter.ts'), adapterSource);
  const { NativeBleAdapter: PreviousAdapter } = await import(pathToFileURL(join(temporary, 'adapter.ts')).href);
  const data = (length: number) => Uint8Array.from({ length }, (_, index) => index * 73 & 255);
  const workloads: { name: string; baseline: () => void | Promise<void>; candidate: () => void | Promise<void>; iterations: number }[] = [];
  for (const length of [0, 1, 2, 20]) {
    const bytes = data(length);
    if (previous.fromByteArray(bytes) !== encodeBase64(bytes)) throw new Error('Encoding mismatch.');
    workloads.push({ name: `encode ${length} B`, iterations: 200_000,
      baseline: () => { sink += previous.fromByteArray(bytes).length; },
      candidate: () => { sink += encodeBase64(bytes).length; } });
  }
  for (const length of [7, 20, 40, 512, 65_536]) {
    const encoded = previous.fromByteArray(data(length));
    if (!Buffer.from(decodeBase64(encoded)).equals(Buffer.from(previous.toByteArray(encoded)))) throw new Error('Decoding mismatch.');
    workloads.push({ name: `owned decode ${length} B`, iterations: Math.max(100, Math.floor(4_000_000 / length)),
      baseline: () => { sink += previous.toByteArray(encoded).slice()[0]!; },
      candidate: () => { sink += decodeBase64(encoded)[0]!; } });
  }
  const reject = (decode: (value: string) => Uint8Array) => {
    try { decode('A'); } catch { sink++; return; }
    throw new Error('Expected an invalid-length rejection.');
  };
  workloads.push({ name: 'reject invalid length', iterations: 10_000,
    baseline: () => reject(previous.toByteArray), candidate: () => reject(decodeBase64) });
  const notifications = [captures.info, captures.id, captures.brightness, captures.cct]
    .flatMap(capture => {
      const bytes = Buffer.from(capture.rx, 'hex');
      return [bytes.subarray(0, 7), bytes.subarray(7)].map(part => part.toString('base64'));
    });
  type Adapter = Pick<NativeBleAdapter, 'connect' | 'subscribe' | 'write' | 'disconnect'>;
  const utf8 = new TextEncoder();
  async function native(AdapterClass: new (manager: BleManager, address: string) => Adapter) {
    let notify: ((error: null, characteristic: { value: string }) => void) | undefined;
    const device = {
      discoverAllServicesAndCharacteristics: async () => {},
      services: async () => [{ uuid: G60_SERVICE }],
      characteristicsForService: async () => [
        { uuid: G60_WRITE, isWritableWithResponse: true },
        { uuid: G60_NOTIFY, isNotifiable: true },
      ],
      monitorCharacteristicForService: (_service: string, _characteristic: string, callback: typeof notify) => {
        notify = callback; return { remove() {} };
      },
      writeCharacteristicWithResponseForService: async (_service: string, _characteristic: string, value: string) => {
        // Native interop reads the contents, so include string materialization.
        const payload = utf8.encode(value);
        sink += payload.length + payload[0]!;
      },
    };
    const manager = {
      connectToDevice: async () => device,
      onDeviceDisconnected: () => ({ remove() {} }),
      cancelDeviceConnection: async () => {},
    };
    const adapter = new AdapterClass(manager as unknown as BleManager, 'fixture');
    await adapter.connect();
    await adapter.subscribe(bytes => { sink += bytes[0]!; });
    return { adapter, emit: (value: string) => notify!(null, { value }) };
  }
  const baselineNative = await native(PreviousAdapter);
  const candidateNative = await native(NativeBleAdapter);
  workloads.push({ name: 'adapter captured notification batch', iterations: 40_000,
    baseline: () => { for (const value of notifications) baselineNative.emit(value); },
    candidate: () => { for (const value of notifications) candidateNative.emit(value); } });
  const write = data(20);
  workloads.push({ name: 'adapter acknowledged write 20 B', iterations: 40_000,
    baseline: () => baselineNative.adapter.write(write), candidate: () => candidateNative.adapter.write(write) });
  const summary = (numbers: number[]) => {
    const sorted = numbers.toSorted((a, b) => a - b);
    return { medianNs: sorted[Math.floor(sorted.length / 2)]!, p95Ns: sorted[Math.ceil(sorted.length * .95) - 1]!,
      minNs: sorted[0]!, maxNs: sorted.at(-1)! };
  };
  const environment = { bun: Bun.version, platform: process.platform, arch: process.arch, cpu: cpus()[0]?.model,
    cpuCount: cpus().length, initialLoad: loadavg(), baselineRef: values['baseline-ref'], samples,
    note: 'In-memory warm steady-state; alternating order; native GATT mocked; install, imports and compilation excluded.' };
  const results = [];
  for (const workload of workloads) {
    const asynchronous = workload.name.includes('acknowledged');
    async function measure(run: () => void | Promise<void>, iterations: number) {
      const cpuStart = process.cpuUsage();
      const start = performance.now();
      if (asynchronous) { for (let index = 0; index < iterations; index++) await run(); }
      else { for (let index = 0; index < iterations; index++) run(); }
      const wall = (performance.now() - start) * 1_000_000 / iterations;
      const cpu = process.cpuUsage(cpuStart);
      return { wall, cpu: (cpu.user + cpu.system) * 1_000 / iterations };
    }
    await measure(workload.baseline, workload.iterations);
    await measure(workload.candidate, workload.iterations);
    const baseline: number[] = [], candidate: number[] = [], baselineCpu: number[] = [], candidateCpu: number[] = [];
    const record = (result: { wall: number; cpu: number }, wall: number[], cpu: number[]) => {
      wall.push(result.wall); cpu.push(result.cpu);
    };
    for (let sample = 0; sample < samples; sample++) {
      if (sample % 2 === 0) {
        record(await measure(workload.baseline, workload.iterations), baseline, baselineCpu);
        record(await measure(workload.candidate, workload.iterations), candidate, candidateCpu);
      } else {
        record(await measure(workload.candidate, workload.iterations), candidate, candidateCpu);
        record(await measure(workload.baseline, workload.iterations), baseline, baselineCpu);
      }
    }
    const old = summary(baseline), next = summary(candidate);
    results.push({ workload: workload.name, iterationsPerSample: workload.iterations,
      baseline: old, candidate: next, baselineCpu: summary(baselineCpu), candidateCpu: summary(candidateCpu),
      medianRatio: next.medianNs / old.medianNs, baselineSamplesNs: baseline, candidateSamplesNs: candidate,
      baselineCpuSamplesNs: baselineCpu, candidateCpuSamplesNs: candidateCpu });
  }
  await baselineNative.adapter.disconnect();
  await candidateNative.adapter.disconnect();
  console.log(JSON.stringify({ environment, results, finalLoad: loadavg(), sink }, null, 2));
} finally {
  await rm(temporary, { recursive: true, force: true });
}
