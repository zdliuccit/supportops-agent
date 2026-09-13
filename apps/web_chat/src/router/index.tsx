import { BrowserRouter, Navigate, Outlet, Route, Routes, useParams } from "react-router-dom";

import ChatPage from "../pages/chat";
import { AgentManagementPage } from "../pages/agent-management/agents";
import { AgentDashboardPage, SystemDashboardPage } from "../pages/agent-management/dashboard";
import { ErrorAnalysisPage } from "../pages/analytics/errors";
import { AnalyticsAgentStatusPage, AnalyticsRankingsPage } from "../pages/analytics";
import { AnalyticsRunsPage } from "../pages/analytics/runs";
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

function LegacyAgentDashboardRedirect() {
  const { agentId } = useParams();
  return <Navigate to={agentId ? `/analytics/agents/${agentId}` : "/analytics/agents"} replace />;
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/agents" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute><Outlet /></ProtectedRoute>}>
          <Route element={<MainLayout />}>
            <Route path="/agents" element={<AgentCatalogPage />} />
            <Route path="/analytics">
              <Route path="dashboard" element={<SystemDashboardPage />} />
              <Route path="rankings" element={<AnalyticsRankingsPage />} />
              <Route path="agents" element={<AnalyticsAgentStatusPage />} />
              <Route path="agents/:agentId" element={<AgentDashboardPage />} />
              <Route path="runs" element={<AnalyticsRunsPage />} />
              <Route path="errors" element={<ErrorAnalysisPage />} />
            </Route>
            <Route path="/agent-management">
              <Route path="dashboard" element={<Navigate to="/analytics/dashboard" replace />} />
              <Route path="models" element={<ModelManagementPage />} />
              <Route path="models/:modelId" element={<ModelManagementPage />} />
              <Route path="agents" element={<AgentManagementPage />} />
              <Route path="agents/:agentId" element={<AgentManagementPage />} />
              <Route path="agents/:agentId/dashboard" element={<LegacyAgentDashboardRedirect />} />
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
