import { expect, test } from 'bun:test';
import { isShowcaseNavigation, parseShowcaseEvent, showcaseCommandScript, SHOWCASE_ASSET_URL } from './bridge';

test('viewer messages cannot request native operations or forge a spin state', () => {
  expect(parseShowcaseEvent('{"type":"ready"}')).toEqual({ type: 'ready' });
  expect(parseShowcaseEvent('{"type":"state","spinning":false}')).toEqual({ type: 'state', spinning: false });
  for (const message of ['null', '[]', 'oops', '{"type":"connect"}', '{"type":"state","spinning":"false"}', ' '.repeat(1025)]) {
    expect(parseShowcaseEvent(message)).toBeNull();
  }
});

test('viewer navigation cannot leave its bundled local document', () => {
  expect(isShowcaseNavigation('about:blank')).toBe(true);
  expect(isShowcaseNavigation(SHOWCASE_ASSET_URL)).toBe(true);
  expect(isShowcaseNavigation(SHOWCASE_ASSET_URL + '#reduce-motion')).toBe(true);
  for (const url of ['https://example.com/', 'file:///sdcard/test', 'intent://settings', 'https://lighttime.invalid.evil/', 'https://lighttime.invalid/?load=remote']) {
    expect(isShowcaseNavigation(url)).toBe(false);
  }
});

test('only the viewer command bridge executes preset controls', () => {
  const received: string[] = [];
  new Function('window', showcaseCommandScript('back'))({ G60Showcase: { command: (action: string) => { received.push(action); } } });
  expect(received).toEqual(['back']);
  expect(() => new Function('window', showcaseCommandScript('dispose'))({})).not.toThrow();
});
