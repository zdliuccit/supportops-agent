/**
 * 在调用方已经同步设置 Loading 后，等待浏览器完成加载态渲染再发起请求。
 * 调用顺序必须是 setLoading(true) -> delayRequest(request)。
 */
export async function delayRequest<T>(
  request: () => Promise<T>,
  delayMs = 300,
): Promise<T> {
  await new Promise<void>((resolve) => {
    window.setTimeout(resolve, delayMs);
  });
  return request();
}
