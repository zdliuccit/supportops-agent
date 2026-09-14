import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useParams } from "react-router-dom";

import { ProtectedRoute } from "../components/ProtectedRoute";
import { Toaster } from "../components/ui/sonner";
import { MainLayout } from "../layouts/MainLayout";

const ChatPage = lazy(() => import("../pages/chat"));
const AgentManagementPage = lazy(() => import("../pages/agent-management/agents").then((module) => ({ default: module.AgentManagementPage })));
const AgentDashboardPage = lazy(() => import("../pages/agent-management/dashboard").then((module) => ({ default: module.AgentDashboardPage })));
const SystemDashboardPage = lazy(() => import("../pages/agent-management/dashboard").then((module) => ({ default: module.SystemDashboardPage })));
const ErrorAnalysisPage = lazy(() => import("../pages/analytics/errors").then((module) => ({ default: module.ErrorAnalysisPage })));
const AnalyticsAgentStatusPage = lazy(() => import("../pages/analytics").then((module) => ({ default: module.AnalyticsAgentStatusPage })));
const AnalyticsRankingsPage = lazy(() => import("../pages/analytics").then((module) => ({ default: module.AnalyticsRankingsPage })));
const AnalyticsRunsPage = lazy(() => import("../pages/analytics/runs").then((module) => ({ default: module.AnalyticsRunsPage })));
const ToolManagementPage = lazy(() => import("../pages/agent-management/tools").then((module) => ({ default: module.ToolManagementPage })));
const ModelManagementPage = lazy(() => import("../pages/agent-management/models").then((module) => ({ default: module.ModelManagementPage })));
const AgentCatalogPage = lazy(() => import("../pages/agents").then((module) => ({ default: module.AgentCatalogPage })));
const UserManagementPage = lazy(() => import("../pages/enterprise/users").then((module) => ({ default: module.UserManagementPage })));
const LoginPage = lazy(() => import("../pages/login").then((module) => ({ default: module.LoginPage })));
const NotFoundPage = lazy(() => import("../pages/not-found").then((module) => ({ default: module.NotFoundPage })));
const CompanyInfoPage = lazy(() => import("../pages/enterprise/company").then((module) => ({ default: module.CompanyInfoPage })));
const DepartmentManagementPage = lazy(() => import("../pages/enterprise/departments").then((module) => ({ default: module.DepartmentManagementPage })));
const KnowledgeManagementPage = lazy(() => import("../pages/knowledge").then((module) => ({ default: module.KnowledgeManagementPage })));
const KnowledgeDetailPage = lazy(() => import("../pages/knowledge").then((module) => ({ default: module.KnowledgeDetailPage })));

function RouteLoading() {
  return <div className="grid min-h-[280px] place-items-center text-sm text-[#919eab]">正在加载页面…</div>;
}

function LegacyAgentDashboardRedirect() {
  const { agentId } = useParams();
  return <Navigate to={agentId ? `/analytics/agents/${agentId}` : "/analytics/agents"} replace />;
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <Suspense fallback={<RouteLoading />}>
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
            <Route path="/knowledge" element={<KnowledgeManagementPage />} />
            <Route path="/knowledge/:documentId" element={<KnowledgeDetailPage />} />
            <Route path="/agents/:agentId/chat" element={<ChatPage embedded />} />
            <Route path="/agents/:agentId/chat/c/:conversationId" element={<ChatPage embedded />} />
          </Route>
          <Route path="/agents/:agentId/chat/standalone" element={<ChatPage standalone />} />
          <Route path="/agents/:agentId/chat/standalone/c/:conversationId" element={<ChatPage standalone />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
      <Toaster />
    </BrowserRouter>
  );
}
