import { Navigate } from "react-router-dom";

/**
 * Legacy route: opens Processes with the add side panel. Prefer `/processes` and opening the panel in place.
 */
export default function AddProcess() {
  return <Navigate to="/processes" replace state={{ openAddProcess: true }} />;
}
