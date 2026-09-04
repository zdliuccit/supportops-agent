import { createContext, useContext } from "react";

import type { Identity } from "@/types";

export const IdentityContext = createContext<Identity | null>(null);

/** 读取当前受保护路由已经验证过的登录用户身份。 */
export function useIdentity(): Identity | null {
  return useContext(IdentityContext);
}
