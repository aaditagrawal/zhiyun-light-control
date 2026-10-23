import { expect, test } from 'bun:test';
import type { EffectConfig } from '@lighttime/sdk';
import { createEffectForm, EFFECT_CHOICES, effectConfigFromForm, supportsUntilStopped } from '../src/effectForm';
import { draftsFromFrames, keyframesFromDrafts, MAX_KEYFRAMES, nextKeyframeAt } from '../src/pattern-ui/keyframes';
import { colors as c } from '../src/pattern-ui/palette';
import { PREVIEW_BASELINE, previewEffect, temperatureColor } from '../src/pattern-ui/preview';

// Execute the actual panel, editor and input components. Local hook cells model
// disclosure unmount/remount without loading React Native or changing global modules.
const transpiler = new Bun.Transpiler({ loader: 'tsx', tsconfig: {
  compilerOptions: { jsx: 'react', jsxFactory: 'element', jsxFragmentFactory: 'Fragment' },
} });
const sources = await Promise.all(['PatternPanel.tsx', 'pattern-ui/KeyframeEditor.tsx', 'pattern-ui/TuningControls.tsx']
  .map(async path => transpiler.transformSync((await Bun.file(new URL(`../src/${path}`, import.meta.url)).text())
    .replace(/^import[\s\S]*?;\n/gm, '')).replace(/^export /gm, '')));

type Node = { type: string | ((props: any) => Node); props: Record<string, any>; children: (Node | string)[] };
function element(type: Node['type'], props: Record<string, any> | null, ...children: any[]): Node {
  return { type, props: props ?? {}, children: children.flat(Infinity).filter(child => child != null && child !== false) };
}
function text(node: Node): string { return node.children.map(child => typeof child === 'object' ? text(child) : child).join(' '); }
function find(node: Node, predicate: (node: Node) => boolean): Node {
  if (predicate(node)) return node;
  for (const child of node.children) {
    if (typeof child !== 'object') continue;
    try { return find(child, predicate); } catch { /* try the next sibling */ }
  }
  throw new Error('UI element not found');
}

function fixture() {
  type Hooks = { cells: any[]; cursor: number };
  let active: Hooks;
  const mounts = new Map<string, Hooks>();
  const useState = (initial: any) => {
    const { cells } = active, index = active.cursor++;
    if (!(index in cells)) cells[index] = typeof initial === 'function' ? initial() : initial;
    return [cells[index], (value: any) => { cells[index] = typeof value === 'function' ? value(cells[index]) : value; }];
  };
  const useRef = (initial: any) => active.cells[active.cursor++] ??= { current: initial };
  const useMemo = (calculate: () => any) => calculate();
  const shared = { element, Fragment: 'Fragment', useState, useRef, useMemo, c,
    View: 'View', Text: 'Text', TextInput: 'TextInput', Pressable: 'Pressable', Slider: 'Slider', Switch: 'Switch',
    StyleSheet: { create: (styles: unknown) => styles }, PREVIEW_BASELINE, temperatureColor };
  const load = (source: string, dependencies: Record<string, any>, result: string) => {
    const values = { ...shared, ...dependencies };
    return new Function(...Object.keys(values), `${source}\nreturn ${result};`)(...Object.values(values));
  };
  const controls = load(sources[2]!, {}, '{ Chip, Field, Range }');
  const KeyframeEditor = load(sources[1]!, { ...controls, MAX_KEYFRAMES, nextKeyframeAt }, 'KeyframeEditor');
  const Panel = load(sources[0]!, { ...controls, KeyframeEditor,
    PatternBrowser: 'PatternBrowser', ScenePreview: 'ScenePreview', createEffectForm, EFFECT_CHOICES,
    effectConfigFromForm, supportsUntilStopped, previewEffect, draftsFromFrames, keyframesFromDrafts }, 'PatternPanel');
  const started: EffectConfig[] = [];
  const props = { ready: true, working: false, connected: true,
    status: { running: false, id: null, pattern: null, progress: null, endsAt: null, lastError: null },
    onStart: async (config: EffectConfig) => { started.push(config); }, onStop: async () => {} };
  const render = () => {
    const seen = new Set<string>();
    const expand = (node: Node, path: string): Node => {
      if (typeof node.type === 'function') {
        const id = `${path}/${node.type.name}`;
        seen.add(id);
        active = mounts.get(id) ?? { cells: [], cursor: 0 };
        active.cursor = 0;
        mounts.set(id, active);
        return expand(node.type(node.props), `${id}/output`);
      }
      return { ...node, children: node.children.map((child, index) => typeof child === 'object'
        ? expand(child, `${path}/${child.props.key ?? index}`) : child) };
    };
    const tree = expand(element(Panel, props), 'panel');
    for (const key of mounts.keys()) if (!seen.has(key)) mounts.delete(key);
    return tree;
  };
  const choose = (id: string) => find(render(), node => node.type === 'PatternBrowser').props.onSelect(id);
  const toggle = () => find(render(), node => node.type === 'Pressable' && text(node).includes('Tune pattern')).props.onPress();
  const input = (label: string) => find(render(), node => node.type === 'TextInput' && node.props.accessibilityLabel === label).props;
  const start = () => find(render(), node => node.type === 'Pressable' && text(node) === 'Start pattern').props;
  return { render, choose, toggle, input, start, started };
}

test('blank frame time survives real Tune unmount/remount and stays invalid until corrected', async () => {
  const app = fixture();
  app.choose('keyframes');
  app.toggle();
  const label = 'Frame 2 time in seconds';
  app.input(label).onChangeText('');
  expect(app.start().disabled).toBe(true);
  app.toggle();
  expect(() => app.input(label)).toThrow();
  expect(app.start().disabled).toBe(true);
  app.toggle();
  expect(app.input(label).value).toBe('');
  expect(app.start().disabled).toBe(true);
  app.input(label).onChangeText('2.5');
  expect(app.start().disabled).toBe(false);
  await app.start().onPress();
  const config = app.started[0]!;
  if (config.pattern !== 'keyframes') throw new Error('Expected keyframes');
  expect(config.keyframes[1]!.at).toBe(2.5);
});

test('invalid optional strings survive collapse and unrelated edits without becoming valid carry values', () => {
  for (const [label, value, repaired] of [
    ['Frame 2 brightness percent', '.', '42'], ['Frame 2 temperature kelvin', '5e', '5600'],
  ]) {
    const app = fixture();
    app.choose('keyframes');
    app.toggle();
    app.input(label!).onChangeText(value);
    app.toggle();
    app.toggle();
    expect(app.input(label!).value).toBe(value!);
    expect(app.start().disabled).toBe(true);
    app.input('Frame 2 time in seconds').onChangeText('2.5');
    expect(app.start().disabled).toBe(true);
    app.input(label!).onChangeText(repaired);
    expect(app.start().disabled).toBe(false);
  }
});

test('valid edit spelling survives collapse and same scene selection; different scene resets drafts', () => {
  const app = fixture();
  app.choose('keyframes');
  app.toggle();
  const label = 'Frame 2 time in seconds';
  app.input(label).onChangeText('2.50');
  app.toggle();
  app.choose('keyframes');
  app.toggle();
  expect(app.input(label).value).toBe('2.50');
  app.choose('candle');
  app.choose('keyframes');
  expect(app.input(label).value).toBe('2');
  expect(app.start().disabled).toBe(false);
});
