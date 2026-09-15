import { Navigate } from "react-router-dom";

export function LegacyDashboardRedirect() {
 return <Navigate to="/analytics/dashboard" replace />;
}
