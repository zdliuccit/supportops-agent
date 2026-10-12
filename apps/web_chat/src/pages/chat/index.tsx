import { Suspense } from "react";
import { Outlet } from "react-router-dom";

import { ChatWorkspace } from "./components/ChatWorkspace";

export default function ChatRoute() {
  return (
    <>
      <ChatWorkspace embedded />
      <Suspense fallback={null}><Outlet /></Suspense>
    </>
  );
}
