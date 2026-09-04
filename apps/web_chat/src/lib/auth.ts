import { ApiError } from "../api";

// 切换存储版本，确保旧的自动开发令牌不会绕过登录页。
const TOKEN_STORAGE_KEY = "supportops-access-token-v4";

/** 清除过期、失效或声明版本不兼容的登录令牌。 */
export function clearAccessToken() {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

/** 保存登录接口签发的访问令牌。 */
export function saveAccessToken(token: string) {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

/** 读取显式登录产生的令牌；不存在时不再自动创建身份。 */
export function getStoredAccessToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function getAccessToken(): Promise<string> {
  const stored = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (stored === null) return Promise.reject(new ApiError("请先登录", 401));
  return Promise.resolve(stored);
}

/** 执行需要认证的操作，并在服务端返回 401 时清除令牌并通知路由守卫。 */
export async function withRefreshedToken<T>(operation: (token: string) => Promise<T>) {
  const token = await getAccessToken();
  try {
    return { token, value: await operation(token) };
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) {
      clearAccessToken();
      window.dispatchEvent(new Event("supportops:unauthorized"));
    }
    throw cause;
  }
}
