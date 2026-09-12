import { Navigate, useLocation } from "react-router-dom";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { profileRouteRedirect } from "../../domain/profile-capabilities";

/**
 * Keeps retail automation routes (default, GFI) separate from Middleby analytics routes.
 * Renders children when the active profile is allowed on the current path.
 */
export default function ProfileRouteGuard({ children }) {
  const { profileId } = useDemoProfile();
  const { pathname } = useLocation();
  const redirect = profileRouteRedirect(profileId, pathname);

  if (redirect) {
    return <Navigate to={redirect} replace />;
  }

  return children;
}
