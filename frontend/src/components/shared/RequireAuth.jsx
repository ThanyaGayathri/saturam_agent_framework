import { Navigate, Outlet, useLocation } from "react-router-dom";
import { usePrototypeAuth } from "../../context/PrototypeAuthContext";

function AuthLoading() {
  return (
    <div className="flex h-screen items-center justify-center bg-surface">
      <p className="text-sm text-text-secondary">Checking access…</p>
    </div>
  );
}

/** Requires server session when prototype login is enabled (see Vercel env). */
export default function RequireAuth() {
  const { ready, authEnabled, isAuthenticated } = usePrototypeAuth();
  const location = useLocation();

  if (!ready) {
    return <AuthLoading />;
  }

  if (!authEnabled) {
    return <Outlet />;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }

  return <Outlet />;
}
