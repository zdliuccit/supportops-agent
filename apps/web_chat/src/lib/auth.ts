import { ApiError, logoutSession } from "../api";

const TOKEN_STORAGE_KEY = "supportops-auth-session-v1";
const LOGOUT_SIGNAL_KEY = "supportops-auth-logout-signal-v1";
let observedToken: string | null = null;
let expiryTimer: number | undefined;
interface SessionClaims { jti: string; sub: string; tenant_id: string; exp: number }

/** 客户端声明只用于界面收口，API 签名/数据库是唯一授权依据。 */
function claimsOf(token: string): SessionClaims | null {
  try {
    const encoded = token.split(".")[1];
    if (!encoded || encoded.length > 16384) return null;
    const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
    const claims: unknown = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=")));
    if (!claims || typeof claims !== "object") return null;
    const value = claims as Record<string, unknown>;
    if (typeof value.jti !== "string" || typeof value.sub !== "string" || typeof value.tenant_id !== "string" || typeof value.exp !== "number" || !Number.isSafeInteger(value.exp * 1000) || value.exp <= 0) return null;
    return { jti: value.jti, sub: value.sub, tenant_id: value.tenant_id, exp: value.exp };
  } catch { return null; }
}

function signalLogout(token: string | null, all = false) {
  const claims = token ? claimsOf(token) : null;
  if (!claims) return;
  // localStorage 只传递非秘密信号，令牌只在 sessionStorage。
  try { localStorage.setItem(LOGOUT_SIGNAL_KEY, JSON.stringify({ session_id: claims.jti, user_id: claims.sub, tenant_id: claims.tenant_id, all, nonce: crypto.randomUUID() })); }
  catch { /* 服务器撤销仍有效，跨标签信号不可用时由下次认证收口。 */ }
}

/** 清理当前页认证缓存，不声称已撤销服务器会话。 */
export function clearAccessToken({ broadcast = true, all = false, deferNotification = false } = {}) {
  const token = sessionStorage.getItem(TOKEN_STORAGE_KEY);
  sessionStorage.removeItem(TOKEN_STORAGE_KEY); localStorage.removeItem("supportops-access-token-v4");
  observedToken = null;
  if (expiryTimer !== undefined) window.clearTimeout(expiryTimer);
  expiryTimer = undefined;
  if (broadcast) signalLogout(token, all);
  const notifyClear = () => window.dispatchEvent(new Event("supportops:auth-cleared"));
  if (deferNotification) queueMicrotask(() => { if (!sessionStorage.getItem(TOKEN_STORAGE_KEY)) notifyClear(); });
  else notifyClear();
}

function scheduleExpiry(token: string) {
  if (token === observedToken) return;
  if (expiryTimer !== undefined) window.clearTimeout(expiryTimer);
  observedToken = token;
  const claims = claimsOf(token), remaining = claims ? claims.exp * 1000 - Date.now() : 0;
  expiryTimer = window.setTimeout(() => {
    if (sessionStorage.getItem(TOKEN_STORAGE_KEY) !== token) return;
    clearAccessToken(); window.dispatchEvent(new Event("supportops:unauthorized"));
  }, Math.max(0, Math.min(remaining, 2147483647)));
}

/** 只保存显式登录结果，无自动身份或刷新令牌。 */
export function saveAccessToken(token: string) {
  localStorage.removeItem("supportops-access-token-v4"); sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
  observedToken = null; scheduleExpiry(token);
}

export function getStoredAccessToken(): string | null {
  const token = sessionStorage.getItem(TOKEN_STORAGE_KEY);
  localStorage.removeItem("supportops-access-token-v4");
  if (!token) return null;
  const claims = claimsOf(token);
  if (!claims || claims.exp * 1000 <= Date.now()) { clearAccessToken({ deferNotification: true }); return null; }
  scheduleExpiry(token); return token;
}

export function getAccessToken(): Promise<string> {
  const token = getStoredAccessToken();
  return token ? Promise.resolve(token) : Promise.reject(new ApiError("请先登录", 401));
}

/** 旧请求的 401 不得清掉随后重新登录的会话，也不重放业务写请求。 */
export async function withAuthenticatedToken<T>(operation: (token: string) => Promise<T>) {
  const token = await getAccessToken();
  try { return { token, value: await operation(token) }; }
  catch (cause) {
    if (cause instanceof ApiError && cause.status === 401 && sessionStorage.getItem(TOKEN_STORAGE_KEY) === token) {
      expireAccessToken(token);
    }
    throw cause;
  }
}

/** 历史名称兼容，仅认证与 401 收口，实际不执行刷新。 */
export const withRefreshedToken = withAuthenticatedToken;

/** 失效事件必须对应原令牌，避免迟到请求清理新登录。 */
export function expireAccessToken(token: string) {
  if (sessionStorage.getItem(TOKEN_STORAGE_KEY) !== token) return;
  clearAccessToken(); window.dispatchEvent(new Event("supportops:unauthorized"));
}

/** 撤销另一会话后通知持有相同 jti 的标签；不传播令牌。 */
export function notifyRevokedSession(sessionId: string) {
  const token = sessionStorage.getItem(TOKEN_STORAGE_KEY), claims = token ? claimsOf(token) : null;
  if (!claims) return;
  try { localStorage.setItem(LOGOUT_SIGNAL_KEY, JSON.stringify({ session_id: sessionId, user_id: claims.sub,
    tenant_id: claims.tenant_id, all: false, nonce: crypto.randomUUID() })); }
  catch { /* 信号失败由 API 下次认证收口。 */ }
}

/** 同源标签只收口对应会话或同用户的明确全部撤销。 */
export function subscribeAuthChanges(onClear: () => void): () => void {
  const cleared = () => onClear();
  const storage = (event: StorageEvent) => {
    if (event.key !== LOGOUT_SIGNAL_KEY || !event.newValue) return;
    const token = sessionStorage.getItem(TOKEN_STORAGE_KEY), claims = token ? claimsOf(token) : null;
    try {
      const message: unknown = JSON.parse(event.newValue);
      if (!claims || !message || typeof message !== "object") return;
      const value = message as Record<string, unknown>;
      if (value.session_id === claims.jti || value.all === true && value.user_id === claims.sub && value.tenant_id === claims.tenant_id) clearAccessToken({ broadcast: false });
    } catch { /* 非秘密信号无效时不能改变服务器授权。 */ }
  };
  window.addEventListener("supportops:auth-cleared", cleared); window.addEventListener("storage", storage);
  return () => { window.removeEventListener("supportops:auth-cleared", cleared); window.removeEventListener("storage", storage); };
}

/** 退出先尝试服务器撤销；失败仍清理本页，由调用方说明未确认。 */
export async function logoutCurrentSession(): Promise<boolean> {
  const token = sessionStorage.getItem(TOKEN_STORAGE_KEY);
  let confirmed = token === null;
  try { if (token) { await logoutSession(token); confirmed = true; } }
  catch (cause) { if (cause instanceof ApiError && cause.status === 401) confirmed = true; }
  finally { if (sessionStorage.getItem(TOKEN_STORAGE_KEY) === token) clearAccessToken(); }
  return confirmed;
}
