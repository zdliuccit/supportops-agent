import { useLayoutEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { ApiError, getIdentity } from "@/api";
import { GlobalLoading } from "@/components/GlobalLoading";
import { IdentityContext } from "@/components/IdentityContext";
import { Button } from "@/components/ui/button";
import { expireAccessToken, getStoredAccessToken, subscribeAuthChanges } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import { useAppDispatch } from "@/store/hooks";
import { clearOrganizationUnits } from "@/store/organizationUnitsSlice";
import type { Identity } from "@/types";

let validatedToken: string | null = null;
let cachedIdentity: Identity | null = null;

/** 在任何业务页面渲染前验证服务端身份，避免未认证内容闪现。 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const location = useLocation();
  const dispatch = useAppDispatch();
  const tokenAtMount = getStoredAccessToken();
  const initialIdentity = tokenAtMount === validatedToken ? cachedIdentity : null;
  const [routeStatus, setRouteStatus] = useState<"checking" | "authenticated" | "anonymous" | "unavailable">(
    tokenAtMount ? (initialIdentity ? "authenticated" : "checking") : "anonymous",
  );
  const [identity, setIdentity] = useState<Identity | null>(initialIdentity);
  const [retry, setRetry] = useState(0);

  useLayoutEffect(() => {
    let cancelled = false, checking = false;
    const clear = () => {
      validatedToken = null; cachedIdentity = null; setIdentity(null);
      dispatch(clearOrganizationUnits()); setRouteStatus("anonymous");
    };
    const unsubscribe = subscribeAuthChanges(clear);
    const unauthorized = () => notify.warning("登录状态已失效，请重新登录。", { id: "session-expired" });
    window.addEventListener("supportops:unauthorized", unauthorized);
    async function validate() {
      if (checking || cancelled) return;
      const token = getStoredAccessToken();
      if (!token) { clear(); return; }
      checking = true;
      try {
        const [value] = await Promise.all([getIdentity(token), token === validatedToken
          ? Promise.resolve() : new Promise((resolve) => window.setTimeout(resolve, 300))]);
        if (cancelled || getStoredAccessToken() !== token) return;
        validatedToken = token; cachedIdentity = value;
        setIdentity(value); setRouteStatus("authenticated");
      } catch (cause) {
        if (cancelled || getStoredAccessToken() !== token) return;
        if (cause instanceof ApiError && cause.status === 401) expireAccessToken(token);
        else {
          validatedToken = null; cachedIdentity = null; setIdentity(null);
          dispatch(clearOrganizationUnits()); setRouteStatus("unavailable");
        }
      } finally { checking = false; }
    }
    const focus = () => { if (document.visibilityState === "visible") void validate(); };
    void validate();
    const timer = window.setInterval(focus, 60000);
    window.addEventListener("focus", focus); document.addEventListener("visibilitychange", focus);
    return () => {
      cancelled = true; unsubscribe(); window.clearInterval(timer);
      window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", focus);
      window.removeEventListener("supportops:unauthorized", unauthorized);
    };
  }, [dispatch, retry]);

  if (routeStatus === "checking") {
    return <GlobalLoading label="正在验证身份…" />;
  }
  if (routeStatus === "unavailable") return <main className="grid min-h-svh place-items-center p-6">
    <div role="alert" className="max-w-md space-y-4 text-center">
      <p>暂时无法验证登录状态，请检查网络后重试。</p>
      <Button onClick={() => { setRouteStatus("checking"); setRetry((value) => value + 1); }}>重新验证</Button>
    </div>
  </main>;
  if (routeStatus === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <IdentityContext.Provider value={identity}>{children}</IdentityContext.Provider>;
}
