import { expect, test } from 'bun:test';
import { color, font, minimumTouchTarget, temperatureSwatch, temperatureTrack } from './theme';

// Exercise the actual component's native event callbacks without loading a BLE
// runtime. Like the hook lifecycle tests, local hook cells keep this Bun-only.
const source = (await Bun.file(new URL('./IndicativeSlider.tsx', import.meta.url)).text())
  .replace(/^import .*;\n/gm, '');
const js = new Bun.Transpiler({
  loader: 'tsx', tsconfig: { compilerOptions: { jsx: 'react', jsxFactory: 'element' } },
}).transformSync(source).replace('export function IndicativeSlider', 'function IndicativeSlider');

type Node = { type: string; props: Record<string, any>; children: (Node | string)[] };
function element(type: string, props: Record<string, any>, ...children: any[]): Node {
  return { type, props: props ?? {}, children: children.flat(Infinity).filter(child => child !== null && child !== false) };
}
function find(tree: Node, predicate: (node: Node) => boolean): Node {
  if (predicate(tree)) return tree;
  for (const child of tree.children) {
    if (typeof child !== 'object') continue;
    try { return find(child, predicate); } catch { /* continue through siblings */ }
  }
  throw new Error('UI element not found');
}
function text(tree: Node): string {
  return tree.children.map(child => typeof child === 'object' ? text(child) : child).join(' ');
}

function fixture(kind: 'brightness' | 'temperature' = 'brightness') {
  const cells: any[] = [];
  let cursor = 0;
  let effects: (() => void)[] = [];
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const writes: number[] = [];
  const props = {
    kind, value: kind === 'brightness' ? 50 : 4300, verified: true, working: false, disabled: false,
    onCommit: async (value: number) => { writes.push(value); await held; },
  };
  const dependencies = {
    useState: (initial: any) => {
      const index = cursor++;
      if (!(index in cells)) cells[index] = initial;
      return [cells[index], (value: any) => { cells[index] = value; }];
    },
    useRef: (initial: any) => cells[cursor++] ??= { current: initial },
    useEffect: (effect: () => void, deps: any[]) => {
      const index = cursor++;
      if (!(index in cells) || deps.some((dep, i) => dep !== cells[index][i])) effects.push(effect);
      cells[index] = deps;
    },
    useWindowDimensions: () => ({ width: 390, fontScale: 1 }),
    element, View: 'View', Text: 'Text', Pressable: 'Pressable', Slider: 'Slider',
    StyleSheet: { create: (styles: unknown) => styles },
    color, font, minimumTouchTarget, temperatureSwatch, temperatureTrack,
  };
  const component = new Function(...Object.keys(dependencies), `${js}\nreturn IndicativeSlider;`)(...Object.values(dependencies));
  const render = (): Node => {
    cursor = 0; effects = [];
    const tree = component(props);
    effects.forEach(effect => effect());
    return tree;
  };
  const slider = () => find(render(), node => node.type === 'Slider').props;
  return { props, writes, render, slider, release: async () => { release(); await held; await Promise.resolve(); } };
}

test('touch drafts never overwrite actual readback or write before release; pending writes cannot queue', async () => {
  const app = fixture();
  const slider = app.slider();
  slider.onSlidingStart(50);
  slider.onValueChange(71);
  slider.onValueChange(73);
  expect(app.writes).toEqual([]);
  expect(text(app.render())).toContain('Draft 73%');
  expect(text(app.render())).toContain('50%');
  slider.onSlidingComplete(73);
  slider.onSlidingComplete(80);
  expect(app.writes).toEqual([73]);
  expect(app.slider().disabled).toBe(true);
  expect(text(app.render())).toContain('Requested 73% · checking');
  app.props.value = 73;
  await app.release();
  expect(text(app.render())).toContain('Requested 73% · readback verified');
});

test('a resolved update without verified readback does not claim success or display the request as actual', async () => {
  const app = fixture();
  app.slider().onSlidingStart(50);
  app.slider().onSlidingComplete(90);
  app.props.verified = false;
  app.props.disabled = true;
  await app.release();
  const tree = app.render();
  expect(text(tree)).toContain('Requested 90% · not confirmed');
  const actual = find(tree, node => String(node.props.accessibilityLabel).startsWith('Actual brightness:'));
  expect(actual.props.accessibilityLabel).toContain('50%');
  expect(actual.props.accessibilityLabel).toContain('Unverified');
  expect(app.slider().disabled).toBe(true);
});

test('non-touch Android adjustments commit a single 100 K step without needing a release event', async () => {
  const app = fixture('temperature');
  app.slider().onValueChange(4400);
  expect(app.writes).toEqual([4400]);
  expect(app.slider().disabled).toBe(true);
  app.slider().onValueChange(4500);
  expect(app.writes).toEqual([4400]);
  app.props.value = 4400;
  await app.release();
  expect(text(app.render())).toContain('Requested 4400 K · readback verified');
});
