import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";

import ChatPage from "../pages/chat";
import { AgentManagementPage } from "../pages/agent-management/agents";
import { ToolManagementPage } from "../pages/agent-management/tools";
import { ModelManagementPage } from "../pages/agent-management/models";
import { AgentCatalogPage } from "../pages/agents";
import { UserManagementPage } from "../pages/enterprise/users";
import { LoginPage } from "../pages/login";
import { NotFoundPage } from "../pages/not-found";
import { CompanyInfoPage } from "../pages/enterprise/company";
import { DepartmentManagementPage } from "../pages/enterprise/departments";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { Toaster } from "../components/ui/sonner";
import { MainLayout } from "../layouts/MainLayout";

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/agents" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute><Outlet /></ProtectedRoute>}>
          <Route element={<MainLayout />}>
            <Route path="/agents" element={<AgentCatalogPage />} />
            <Route path="/agent-management">
              <Route path="models" element={<ModelManagementPage />} />
              <Route path="models/:modelId" element={<ModelManagementPage />} />
              <Route path="agents" element={<AgentManagementPage />} />
              <Route path="agents/:agentId" element={<AgentManagementPage />} />
              <Route path="tools" element={<ToolManagementPage />} />
            </Route>
            <Route path="/enterprise">
              <Route path="users" element={<UserManagementPage />} />
              <Route path="company" element={<CompanyInfoPage />} />
              <Route path="departments" element={<DepartmentManagementPage />} />
            </Route>
            <Route path="/agents/:agentId/chat" element={<ChatPage embedded />} />
            <Route path="/agents/:agentId/chat/c/:conversationId" element={<ChatPage embedded />} />
          </Route>
          <Route path="/agents/:agentId/chat/standalone" element={<ChatPage standalone />} />
          <Route path="/agents/:agentId/chat/standalone/c/:conversationId" element={<ChatPage standalone />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
      <Toaster />
    </BrowserRouter>
  );
}
