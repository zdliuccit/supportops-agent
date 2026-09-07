import { useLayoutEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { getIdentity } from "@/api";
import { GlobalLoading } from "@/components/GlobalLoading";
import { IdentityContext } from "@/components/IdentityContext";
import { clearAccessToken, getStoredAccessToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import { useAppDispatch } from "@/store/hooks";
import { clearOrganizationUnits } from "@/store/organizationUnitsSlice";
import type { Identity } from "@/types";

let validatedToken: string | null = null;
let cachedIdentity: Identity | null = null;
const MIN_INITIAL_LOADING_MS = 300;

/** 保证首次身份验证阶段的全局 Loading 可被用户清晰感知。 */
function waitForInitialLoading(): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, MIN_INITIAL_LOADING_MS));
}

/** 在任何业务页面渲染前验证服务端身份，避免未认证内容闪现。 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const location = useLocation();
  const dispatch = useAppDispatch();
  const tokenAtMount = getStoredAccessToken();
  const initialIdentity = tokenAtMount === validatedToken ? cachedIdentity : null;
  const [routeStatus, setRouteStatus] = useState<"checking" | "authenticated" | "anonymous">(
    tokenAtMount ? (tokenAtMount === validatedToken ? "authenticated" : "checking") : "anonymous",
  );
  const [identity, setIdentity] = useState<Identity | null>(initialIdentity);

  useLayoutEffect(() => {
    const token = getStoredAccessToken();
    if (!token) {
      setRouteStatus("anonymous");
      return;
    }
    // 已验证过的令牌在菜单切换时直接复用，避免每次路由切换都出现全屏 Loading 闪烁。
    if (token === validatedToken) setRouteStatus("authenticated");
    let cancelled = false;
    const identityRequest = getIdentity(token);
    const loadingDelay = token === validatedToken ? Promise.resolve() : waitForInitialLoading();
    void Promise.all([identityRequest, loadingDelay])
      .then(([value]) => {
        validatedToken = token;
        cachedIdentity = value;
        setIdentity(value);
        if (!cancelled) setRouteStatus("authenticated");
      })
      .catch(() => {
        if (validatedToken === token) validatedToken = null;
        if (validatedToken === null) cachedIdentity = null;
        setIdentity(null);
        clearAccessToken();
        dispatch(clearOrganizationUnits());
        notify.warning("登录状态已失效，请重新登录。", { id: "session-expired" });
        if (!cancelled) setRouteStatus("anonymous");
      });
    const unauthorized = () => {
      dispatch(clearOrganizationUnits());
      notify.warning("登录状态已失效，请重新登录。", { id: "session-expired" });
      setRouteStatus("anonymous");
    };
    window.addEventListener("supportops:unauthorized", unauthorized);
    return () => {
      cancelled = true;
      window.removeEventListener("supportops:unauthorized", unauthorized);
    };
  }, [dispatch]);

  if (routeStatus === "checking") {
    return <GlobalLoading label="正在验证身份…" />;
  }
  if (routeStatus === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <IdentityContext.Provider value={identity}>{children}</IdentityContext.Provider>;
}
