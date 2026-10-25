import { expect, test } from 'bun:test';
import { isShowcaseNavigation, parseShowcaseEvent, showcaseCommandScript, showcaseUri, SHOWCASE_ASSET_URL, TouchLock } from './bridge';

test('viewer messages cannot request native operations or forge a spin state', () => {
  expect(parseShowcaseEvent('{"type":"ready"}')).toEqual({ type: 'ready' });
  expect(parseShowcaseEvent('{"type":"state","spinning":false}')).toEqual({ type: 'state', spinning: false });
  for (const message of ['null', '[]', 'oops', '{"type":"connect"}', '{"type":"state","spinning":"false"}', '{"type":"diagnostic"}', ' '.repeat(1025)]) {
    expect(parseShowcaseEvent(message)).toBeNull();
  }
});

test('viewer navigation cannot leave its bundled local document', () => {
  expect(isShowcaseNavigation('about:blank')).toBe(true);
  for (const variant of ['', '#reduce-motion', '#inline', '#inline-reduce-motion']) {
    expect(isShowcaseNavigation(SHOWCASE_ASSET_URL + variant)).toBe(true);
  }
  for (const url of ['https://example.com/', 'file:///sdcard/test', 'intent://settings', 'https://lighttime.invalid.evil/',
    'https://lighttime.invalid/?load=remote', SHOWCASE_ASSET_URL + '#inline-evil', SHOWCASE_ASSET_URL + '?inline',
    SHOWCASE_ASSET_URL + '#inline#reduce-motion', 'file:///android_asset/other.html#inline']) {
    expect(isShowcaseNavigation(url)).toBe(false);
  }
});

test('document variants are fixed, and every variant is an allowed navigation', () => {
  expect(showcaseUri({})).toBe(SHOWCASE_ASSET_URL);
  expect(showcaseUri({ reducedMotion: true })).toBe(SHOWCASE_ASSET_URL + '#reduce-motion');
  expect(showcaseUri({ inline: true })).toBe(SHOWCASE_ASSET_URL + '#inline');
  expect(showcaseUri({ inline: true, reducedMotion: true })).toBe(SHOWCASE_ASSET_URL + '#inline-reduce-motion');
  for (const inline of [false, true]) for (const reducedMotion of [false, true]) {
    expect(isShowcaseNavigation(showcaseUri({ inline, reducedMotion }))).toBe(true);
  }
});

test('only the viewer command bridge executes preset controls', () => {
  const received: string[] = [];
  new Function('window', showcaseCommandScript('back'))({ G60Showcase: { command: (action: string) => { received.push(action); } } });
  expect(received).toEqual(['back']);
  expect(() => new Function('window', showcaseCommandScript('dispose'))({})).not.toThrow();
});

test('the parent scroll stays locked until every finger on the model lifts', () => {
  const changes: boolean[] = [];
  const lock = new TouchLock(locked => changes.push(locked));
  lock.start([{ identifier: 1 }], 1);
  lock.start([{ identifier: 2 }], 2);
  lock.end([{ identifier: 1 }], 1);
  expect(lock.active).toBe(true);
  lock.end([{ identifier: 2 }], 0);
  expect(lock.active).toBe(false);
  expect(changes).toEqual([true, false]);
});

test('a reported end that still lists the lifted finger, a cancel or a missed end cannot hold the page', () => {
  const changes: boolean[] = [];
  const lock = new TouchLock(locked => changes.push(locked));
  lock.start([{ identifier: 0 }], 1);
  lock.end([{ identifier: 0 }], 1);
  expect(lock.active).toBe(false);

  lock.start([{ identifier: 3 }], 1);
  lock.start([{ identifier: 4 }], 2);
  lock.release();
  expect(lock.active).toBe(false);

  // An end event was lost: a fresh single touch discards the stale finger.
  lock.start([{ identifier: 5 }], 1);
  lock.start([{ identifier: 6 }], 1);
  lock.end([{ identifier: 6 }], 0);
  expect(lock.active).toBe(false);
  lock.release();
  expect(changes).toEqual([true, false, true, false, true, false]);
});
