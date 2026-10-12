const GRAPHEMES_PER_SECOND = 45;
const RENDER_INTERVAL_MS = 50;

/** 按稳定节奏显示小段正文，回答长度、网络积压和完成事件都不能提高速度。 */
export class ReplyStream {
  private target: string;
  private visible: number;
  private frame: number | null = null;
  private previousTime = 0;
  private credit = 0;
  private lastRenderTime = 0;
  private boundaries: number[] = [];
  private visibleSegments = 0;
  private readonly segmenter = typeof Intl.Segmenter === "function"
    ? new Intl.Segmenter("zh", { granularity: "grapheme" }) : null;
  private disposed = false;
  private listeners = new Set<() => void>();

  constructor(
    content: string,
    visibleLength: number,
    private readonly render: (content: string) => void,
  ) {
    this.target = content;
    this.visible = Math.min(Math.max(0, visibleLength), content.length);
    this.update(content);
  }

  get content() { return this.target; }

  update(content: string) {
    if (this.disposed) return;
    if (!content.startsWith(this.target.slice(0, this.visible))) {
      // 结构化校验重试可能改写答案，旧正文不能与新答案拼接。
      this.visible = 0;
      this.credit = 0;
      this.render("");
    }
    this.target = content;
    // 按用户看到的完整字符计数，组合 emoji、变音符号不会被分成半个字符。
    if (this.segmenter !== null) {
      this.boundaries = Array.from(
        this.segmenter.segment(content), ({ index, segment }) => index + segment.length,
      );
    } else {
      // 旧浏览器按 Unicode 码点降级，至少不截断代理对或阻止订阅回答。
      let offset = 0;
      this.boundaries = Array.from(content, (character) => { offset += character.length; return offset; });
    }
    this.visibleSegments = this.boundaries.findIndex((end) => end > this.visible);
    if (this.visibleSegments < 0) this.visibleSegments = this.boundaries.length;
    if (this.visible < this.target.length && this.frame === null) {
      this.previousTime = performance.now();
      this.lastRenderTime = this.previousTime - RENDER_INTERVAL_MS;
      this.frame = requestAnimationFrame(this.tick);
    }
  }

  private tick = (time: number) => {
    this.frame = null;
    if (this.disposed) return;
    // 标签页休眠、掉帧后不累计追赶时间，恢复可见时继续正常节奏。
    this.credit += Math.max(0, Math.min(RENDER_INTERVAL_MS, time - this.previousTime))
      * GRAPHEMES_PER_SECOND / 1000;
    this.previousTime = time;
    if (time - this.lastRenderTime >= RENDER_INTERVAL_MS) {
      const count = Math.min(3, Math.floor(this.credit), this.boundaries.length - this.visibleSegments);
      if (count > 0) {
        this.credit -= count;
        this.visibleSegments += count;
        this.visible = this.boundaries[this.visibleSegments - 1];
        this.lastRenderTime = time;
        this.render(this.target.slice(0, this.visible));
      }
    }
    if (this.visible < this.target.length) this.frame = requestAnimationFrame(this.tick);
    else {
      this.credit = 0;
      for (const listener of this.listeners) listener();
    }
  };

  async drain(signal: AbortSignal): Promise<void> {
    if (signal.aborted || this.disposed || this.visible === this.target.length) return;
    await new Promise<void>((resolve) => {
      const done = () => {
        this.listeners.delete(done);
        signal.removeEventListener("abort", done);
        resolve();
      };
      this.listeners.add(done);
      signal.addEventListener("abort", done, { once: true });
    });
  }

  dispose() {
    this.disposed = true;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    for (const listener of this.listeners) listener();
    this.listeners.clear();
  }
}

const progressKey = (conversationId: string) => `supportops-reply-progress:${conversationId}`;

export function readReplyProgress(conversationId: string, runId: string): number | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(progressKey(conversationId)) ?? "null");
    if (typeof value === "object" && value !== null && "runId" in value && value.runId === runId
      && "visibleLength" in value && typeof value.visibleLength === "number"
      && Number.isSafeInteger(value.visibleLength) && value.visibleLength >= 0) return value.visibleLength;
  } catch { /* 禁用存储时仍可通过服务端快照恢复。 */ }
  return null;
}

export function saveReplyProgress(conversationId: string, runId: string, visibleLength: number) {
  try { sessionStorage.setItem(progressKey(conversationId), JSON.stringify({ runId, visibleLength })); }
  catch { /* 本地存储不可用不影响接收回答。 */ }
}

export function clearReplyProgress(conversationId: string) {
  try { sessionStorage.removeItem(progressKey(conversationId)); }
  catch { /* 本地存储不可用不影响完成回答。 */ }
}

/** 退避等待可被路由切换或停止按钮即时取消。 */
export function reconnectDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
    const timer = window.setTimeout(done, milliseconds);
    if (signal.aborted) done();
    else signal.addEventListener("abort", done, { once: true });
  });
}
