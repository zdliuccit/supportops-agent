import { useLayoutEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { getIdentity } from "@/api";
import { GlobalLoading } from "@/components/GlobalLoading";
import { IdentityContext } from "@/components/IdentityContext";
import { clearAccessToken, getStoredAccessToken } from "@/lib/auth";
import type { Identity } from "@/types";

let validatedToken: string | null = null;
let cachedIdentity: Identity | null = null;

/** 在任何业务页面渲染前验证服务端身份，避免未认证内容闪现。 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const location = useLocation();
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
    void getIdentity(token)
      .then((value) => {
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
        if (!cancelled) setRouteStatus("anonymous");
      });
    const unauthorized = () => setRouteStatus("anonymous");
    window.addEventListener("supportops:unauthorized", unauthorized);
    return () => {
      cancelled = true;
      window.removeEventListener("supportops:unauthorized", unauthorized);
    };
  }, []);

  if (routeStatus === "checking") {
    return <GlobalLoading label="正在验证身份…" />;
  }
  if (routeStatus === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <IdentityContext.Provider value={identity}>{children}</IdentityContext.Provider>;
}
