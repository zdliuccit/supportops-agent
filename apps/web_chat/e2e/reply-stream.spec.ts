import { expect, test } from "@playwright/test";

import { ReplyStream } from "../src/pages/chat/lib/replyStream";

/** 用可控动画帧验证时间边界，无需真的等待几分钟播放长回答。 */
function frameClock() {
  const originalFrame = globalThis.requestAnimationFrame;
  const originalCancel = globalThis.cancelAnimationFrame;
  const originalNow = Object.getOwnPropertyDescriptor(performance, "now");
  const frames = new Map<number, FrameRequestCallback>();
  let time = 0;
  let nextId = 0;
  globalThis.requestAnimationFrame = (callback) => { frames.set(++nextId, callback); return nextId; };
  globalThis.cancelAnimationFrame = (id) => { frames.delete(id); };
  Object.defineProperty(performance, "now", { configurable: true, value: () => time });

  function frame(milliseconds: number) {
    time += milliseconds;
    const callbacks = [...frames.values()];
    frames.clear();
    for (const callback of callbacks) callback(time);
  }

  return {
    advance(milliseconds: number) {
      const count = Math.floor(milliseconds / (1000 / 60));
      for (let index = 0; index < count; index += 1) frame(1000 / 60);
    },
    resumeAfter(milliseconds: number) { frame(milliseconds); },
    restore() {
      globalThis.requestAnimationFrame = originalFrame;
      globalThis.cancelAnimationFrame = originalCancel;
      if (originalNow) Object.defineProperty(performance, "now", originalNow);
      else Reflect.deleteProperty(performance, "now");
    },
  };
}

test("相同时间内，百字与万字回答的显示量相同，单次显示不超过三个完整字符", () => {
  const clock = frameClock();
  let short = "";
  let long = "";
  let previous = "";
  const increments: number[] = [];
  const first = new ReplyStream("字".repeat(200), 0, (text) => { short = text; });
  const second = new ReplyStream("字".repeat(10_000), 0, (text) => {
    long = text; increments.push(text.length - previous.length); previous = text;
  });
  try {
    clock.advance(2000);
    expect(long).toBe(short);
    expect(long.length).toBeGreaterThanOrEqual(85);
    expect(long.length).toBeLessThanOrEqual(90);
    expect(Math.max(...increments)).toBeLessThanOrEqual(3);
  } finally { first.dispose(); second.dispose(); clock.restore(); }
});

test("网络积压和页面休眠不会跳播，刷新恢复从已显示的位置按同一速度继续", () => {
  const clock = frameClock();
  let displayed = "已显示的正文";
  const prefix = displayed;
  const stream = new ReplyStream(prefix, prefix.length, (text) => { displayed = text; });
  try {
    stream.update(prefix + "字".repeat(10_000));
    clock.advance(1000);
    const firstLength = displayed.length;
    expect(firstLength - prefix.length).toBeLessThanOrEqual(45);
    clock.resumeAfter(60_000);
    expect(displayed.length - firstLength).toBeLessThanOrEqual(3);
    stream.update(prefix + "字".repeat(20_000));
    const before = displayed.length;
    clock.advance(1000);
    expect(displayed.length - before).toBeGreaterThanOrEqual(42);
    expect(displayed.length - before).toBeLessThanOrEqual(46);
  } finally { stream.dispose(); clock.restore(); }
});

test("完成等待不会整篇填入，组合 emoji 和变音符号始终完整", async () => {
  const clock = frameClock();
  const grapheme = "👨‍👩‍👧‍👦";
  const target = (grapheme + "e\u0301").repeat(100);
  let displayed = "";
  let completed = false;
  const stream = new ReplyStream(target, 0, (text) => {
    displayed = text;
    expect(target.startsWith(text)).toBe(true);
    const segments = Array.from(new Intl.Segmenter("zh", { granularity: "grapheme" }).segment(text));
    expect(segments.every(({ segment }) => segment === grapheme || segment === "e\u0301")).toBe(true);
  });
  try {
    const finished = stream.drain(new AbortController().signal).then(() => { completed = true; });
    clock.advance(1000);
    await Promise.resolve();
    expect(completed).toBe(false);
    expect(displayed.length).toBeLessThan(target.length);
    clock.advance(5000);
    await finished;
    expect(displayed).toBe(target);
  } finally { stream.dispose(); clock.restore(); }
});

test("答案改写、取消等待和离开页面后不会泄漏旧内容或继续渲染", async () => {
  const clock = frameClock();
  let displayed = "";
  const stream = new ReplyStream("旧答案".repeat(100), 0, (text) => { displayed = text; });
  try {
    clock.advance(1000);
    stream.update("修正".repeat(100));
    clock.advance(1000);
    expect(displayed).not.toContain("旧答案");
    const controller = new AbortController();
    const cancelled = stream.drain(controller.signal);
    controller.abort();
    await cancelled;
    stream.dispose();
    const last = displayed;
    clock.advance(10_000);
    expect(displayed).toBe(last);
  } finally { stream.dispose(); clock.restore(); }
});

test("缺少 Intl.Segmenter 的旧浏览器仍能逐段显示并保留完整 Unicode 码点", () => {
  const clock = frameClock();
  const original = Object.getOwnPropertyDescriptor(Intl, "Segmenter")!;
  Object.defineProperty(Intl, "Segmenter", { configurable: true, value: undefined });
  let displayed = "";
  const stream = new ReplyStream("🙂".repeat(100), 0, (text) => {
    displayed = text;
    expect(Array.from(text).every((character) => character === "🙂")).toBe(true);
  });
  try {
    clock.advance(1000);
    expect(Array.from(displayed).length).toBeGreaterThanOrEqual(42);
    expect(Array.from(displayed).length).toBeLessThanOrEqual(45);
  } finally {
    stream.dispose(); clock.restore(); Object.defineProperty(Intl, "Segmenter", original);
  }
});
