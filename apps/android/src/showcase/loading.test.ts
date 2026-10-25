import { expect, test } from 'bun:test';
import { ShowcaseLoadBudget } from './loading';

test('loading can make progress past 20 seconds, but repeated progress cannot exceed the total limit', () => {
  let now = 0;
  const budget = new ShowcaseLoadBudget(() => now);
  budget.resume();
  now = 15_000; expect(budget.remaining()).toBe(5_000);
  budget.progress();
  now = 30_000; expect(budget.remaining()).toBe(5_000);
  budget.progress();
  now = 45_000; budget.progress(); expect(budget.remaining()).toBe(15_000);
  now = 60_000; budget.progress(); expect(budget.remaining()).toBe(0);
});

test('background time does not consume either loading limit, and retry receives a fresh budget', () => {
  let now = 0;
  const budget = new ShowcaseLoadBudget(() => now);
  budget.resume(); now = 12_000; budget.pause();
  now = 120_000; expect(budget.remaining()).toBe(8_000);
  budget.resume(); now += 7_000; expect(budget.remaining()).toBe(1_000);
  budget.progress(); expect(budget.remaining()).toBe(20_000);
  budget.pause(); now += 120_000; expect(budget.remaining()).toBe(20_000);
  budget.reset(); budget.resume(); now += 1_000; expect(budget.remaining()).toBe(19_000);
});
