import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useMemo } from "react";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import TourLaunchButton from "../tours/TourLaunchButton";
import {
  catalogPageShell,
  catalogPageInner,
  catalogHeaderRow,
  catalogHeaderText,
  catalogH1,
  catalogSubtitle,
  catalogSectionBelowNav,
  catalogPrimaryAction,
} from "./shared/useCaseCatalogPageStyles";

const TAB_BASE =
  "relative inline-flex items-center gap-2 pb-3 pt-2 text-base font-semibold transition-colors";
const TAB_ACTIVE = "text-primary";
const TAB_IDLE = "text-text-muted hover:text-text-secondary";
const TAB_UNDERLINE_ACTIVE = "bg-primary";
const TAB_UNDERLINE_IDLE = "bg-transparent";

const TAB_COPY = {
  discover: {
    title: "What my data supports",
    description:
      "Metrics your connected data can actually measure, ranked for the areas you care about. Pick the ones you want and build a dashboard from them.",
    tourId: "kpi-registry",
    tourLabel: "Take a quick tour",
  },
  gallery: {
    title: "Data catalog",
    description:
      "Every table the semantic layer exposes, with the attributes and KPIs it actually powers. Read-only — connect and model new sources from Configure.",
    tourId: "kpi-gallery",
    tourLabel: "Take a quick Data catalog tour",
  },
  dashboards: {
    title: "Dashboards",
    description:
      "Dashboards you have built from selected metrics. Every figure is computed against the definition in Configure — change it there and every dashboard follows.",
    tourId: "kpi-dashboard",
    tourLabel: "Take a quick tour",
  },
  dashboard: {
    title: "KPI health",
    description:
      "View health and trends for enabled KPIs. Open Monitor for the live ops view when you are ready.",
    tourId: "kpi-dashboard",
    tourLabel: "Take a quick KPI Dashboard tour",
  },
};

function tabKeyFromPath(pathname) {
  if (pathname.includes("/gallery")) return "gallery";
  if (pathname.includes("/dashboards")) return "dashboards";
  if (pathname.includes("/dashboard")) return "dashboard";
  return "discover";
}

function flattenKpiCount(categories) {
  let n = 0;
  for (const cat of categories || []) {
    for (const sec of cat.sections || []) {
      n += sec.kpis?.length || 0;
    }
  }
  return { total: n };
}

export default function KpisHubLayout() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { data, hydrated } = useKpiRegistry();
  const { total } = useMemo(() => flattenKpiCount(data?.categories), [data?.categories]);

  const tab = tabKeyFromPath(pathname);
  const { title, description, tourId, tourLabel } = TAB_COPY[tab];

  return (
    <div className={catalogPageShell}>
      <div className={catalogPageInner}>
        <div className={catalogHeaderRow}>
          <div className={catalogHeaderText}>
            <div className="flex items-center gap-3">
              <h1 className={catalogH1}>{title}</h1>
              <TourLaunchButton tourId={tourId} label={tourLabel} />
            </div>
            <p className={catalogSubtitle}>{description}</p>
          </div>
          {tab === "discover" ? (
            <div className={catalogPrimaryAction}>
              {/* Opens the discovery page's "Add KPI" modal through the URL (`?add=1`), the way
                  `?run=` and `?unlocked=` already carry page state there -- the modal lives in the
                  page (it needs the areas, the run and the registry), this header does not. The
                  rest of the search string is kept so an in-flight `?run=` survives the click.
                  A `replace`, like the page's own close: this button only shows on the discover
                  tab, so a push would stack a second entry for the same page and leave Back with
                  one dead press once the modal's close has replaced it again. */}
              <button
                type="button"
                onClick={() => {
                  const next = new URLSearchParams(search);
                  next.set("add", "1");
                  navigate({ pathname: "/kpis/discover", search: `?${next.toString()}` }, { replace: true });
                }}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover"
              >
                + Add KPI
              </button>
            </div>
          ) : null}
        </div>

        <div className="mb-8 flex items-center gap-6 border-b border-border" role="tablist" data-tour-kpi="tabs">
          <NavLink
            to="/kpis/discover"
            className={({ isActive }) => `${TAB_BASE} ${isActive ? TAB_ACTIVE : TAB_IDLE}`}
          >
            {({ isActive }) => (
              <>
                What my data supports
                <span
                  aria-hidden
                  className={`absolute inset-x-0 -bottom-px h-0.5 rounded-full transition-colors ${
                    isActive ? TAB_UNDERLINE_ACTIVE : TAB_UNDERLINE_IDLE
                  }`}
                />
              </>
            )}
          </NavLink>
          <NavLink
            to="/kpis/gallery"
            className={({ isActive }) => `${TAB_BASE} ${isActive ? TAB_ACTIVE : TAB_IDLE}`}
          >
            {({ isActive }) => (
              <>
                Data catalog
                <span
                  aria-hidden
                  className={`absolute inset-x-0 -bottom-px h-0.5 rounded-full transition-colors ${
                    isActive ? TAB_UNDERLINE_ACTIVE : TAB_UNDERLINE_IDLE
                  }`}
                />
              </>
            )}
          </NavLink>
          <NavLink
            to="/kpis/dashboards"
            className={({ isActive }) => `${TAB_BASE} ${isActive ? TAB_ACTIVE : TAB_IDLE}`}
          >
            {({ isActive }) => (
              <>
                Dashboards
                <span
                  aria-hidden
                  className={`absolute inset-x-0 -bottom-px h-0.5 rounded-full transition-colors ${
                    isActive ? TAB_UNDERLINE_ACTIVE : TAB_UNDERLINE_IDLE
                  }`}
                />
              </>
            )}
          </NavLink>
        </div>

        <div className={catalogSectionBelowNav}>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
