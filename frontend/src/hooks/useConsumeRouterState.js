import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";

/**
 * Reads `location.state[key]` and clears just that key from history state on the same tick,
 * preserving any other keys -- so a caller can act on a one-shot navigation payload (open this
 * panel, pre-fill this row) without a later back-navigation or refresh replaying it.
 *
 * One shared implementation for a pattern this codebase had already grown three different, only
 * partly compatible copies of: `Processes.jsx` wipes the whole state object, `PlaygroundHomeAlt.jsx`
 * reaches for raw `window.history.replaceState`, and `AnalyticsInsightSession.jsx` has the one
 * correct-but-bespoke version this hook generalizes -- destructure the one key out, keep the rest.
 */
export function useConsumeRouterState(key) {
 const location = useLocation();
 const navigate = useNavigate();
 const value = location.state?.[key];
 useEffect(() => {
  if (value === undefined) return;
  const rest = { ...location.state };
  delete rest[key];
  navigate(location.pathname, { replace: true, state: rest });
  // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only when the state key itself changes
 }, [value, location.pathname, navigate]);
 return value;
}
