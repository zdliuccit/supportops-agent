/** 仅用于连接失败后的退避重连，可被路由切换或停止按钮即时取消。 */
export function reconnectDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
    const timer = window.setTimeout(done, milliseconds);
    if (signal.aborted) done();
    else signal.addEventListener("abort", done, { once: true });
  });
}
