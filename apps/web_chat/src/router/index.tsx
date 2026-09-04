import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";

import App from "../App";
import { AdminAgentsPage } from "../pages/AdminAgentsPage";
import { AdminModelsPage } from "../pages/AdminModelsPage";
import { AgentCatalogPage } from "../pages/AgentCatalogPage";
import { AdminUsersPage } from "../pages/AdminUsersPage";
import { LoginPage } from "../pages/LoginPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { OrganizationPage } from "../pages/OrganizationPage";
import { ProtectedRoute } from "../components/ProtectedRoute";
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
            <Route path="/admin/models" element={<AdminModelsPage />} />
            <Route path="/admin/models/:modelId" element={<AdminModelsPage />} />
            <Route path="/admin/agents" element={<AdminAgentsPage />} />
            <Route path="/admin/agents/:agentId" element={<AdminAgentsPage />} />
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route path="/admin/organization" element={<OrganizationPage />} />
          </Route>
          <Route path="/agents/:agentId/chat" element={<App />} />
          <Route path="/agents/:agentId/chat/c/:conversationId" element={<App />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </BrowserRouter>
  );
}
