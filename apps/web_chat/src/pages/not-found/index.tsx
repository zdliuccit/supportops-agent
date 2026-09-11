import { Link } from "react-router-dom";

import { buttonVariants } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <main className="grid min-h-svh place-items-center bg-[#f7f7f8] px-6 text-center">
      <div>
        <div className="text-6xl font-semibold">404</div>
        <p className="mt-3 text-sm text-muted-foreground">页面不存在，旧版 /chat 路由已移除。</p>
        <Link className={buttonVariants({ className: "mt-6" })} to="/agents">
          返回 Agent 目录
        </Link>
      </div>
    </main>
  );
}
