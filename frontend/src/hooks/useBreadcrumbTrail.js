import { useLocation } from "react-router-dom";

const DEFAULT_ROOT = [{ label: "Playground", to: "/playground" }];

/**
 * Returns the breadcrumb items array for the current page.
 * - If the user navigated here via a drill-down (location.state.trail is set),
 * appends currentLabel to the inherited trail.
 * - Otherwise falls back to a static default trail.
 *
 * @param {string} currentLabel Label for the current page (last, non-linked item)
 * @param {{ label: string, to: string }[]} [fallback] Static trail used when no state trail exists
 */
export function useBreadcrumbTrail(currentLabel, fallback) {
 const location = useLocation();
 const parentTrail = location.state?.trail ?? fallback ?? DEFAULT_ROOT;
 return [...parentTrail, { label: currentLabel }];
}

/**
 * Builds a new trail by appending a step to the current trail.
 * Pass the result as `state.trail` when calling navigate().
 *
 * @param {({ label: string, to?: string }[])} currentTrail The trail on the current page (from useBreadcrumbTrail)
 * @param {string} label Label for the current page being left
 * @param {string} to URL of the current page
 */
export function extendTrail(currentTrail, label, to) {
 // Drop the last item (it has no `to` - it's the current page indicator)
 // then append it as a linked crumb so the next page can link back here.
 const linked = currentTrail.slice(0, -1);
 return [...linked, { label, to }];
}
