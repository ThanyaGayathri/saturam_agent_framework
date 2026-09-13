import { Link, useNavigate, useLocation } from "react-router-dom";
import { PageHeader, Tabs } from "../ui";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { isMiddlebyProfile } from "../../domain/profile-capabilities";
import TourLaunchButton from "../../tours/TourLaunchButton";

// Ordered to match the job, not the org chart: connect something, decide what it measures, then
// organise it. Business hierarchy used to be first and is the one thing you cannot usefully fill
// in before data exists.
//
// "Integrations" is gone as a tab. Databases and platforms were split by how we reach them --
// a connection string versus an API -- which is our concern, not the user's; Salesforce appeared
// under both. Data Sources now lists everything connected, and the platform catalogue is still
// reachable from "Browse platforms" there.
const CONFIGURE_TABS_ALL = [
  { label: "Data Sources", value: "internal", path: "/configure/internal-sources" },
  { label: "KPIs", value: "domain-mapping", path: "/configure/domain-mapping" },
  { label: "Proposals", value: "proposals", path: "/configure/proposals" },
  { label: "Business hierarchy", value: "hierarchy", path: "/configure/hierarchy" },
  { label: "Model", value: "model", path: "/configure/models" },
];

function configureTabsForProfile() {
  return CONFIGURE_TABS_ALL;
}

export default function ConfigureLayout({ children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { profileId } = useDemoProfile();
  const { data, hydrated } = useConfigureWorkspace();
  const configureTabs = configureTabsForProfile(profileId);
  const middleby = isMiddlebyProfile(profileId);

  const normPath = (location.pathname || "/").replace(/\/+$/, "") || "/";
  const isCloudSubRoute = normPath === "/configure/cloud" || normPath === "/configure/cloud-setup";

  const activeTab = location.pathname.startsWith("/configure/hierarchy")
    ? "hierarchy"
    : location.pathname.startsWith("/configure/models")
    ? "model"
    : location.pathname.startsWith("/configure/external")
    ? "external"
    : location.pathname.startsWith("/configure/domain-mapping")
    ? "domain-mapping"
    : location.pathname.startsWith("/configure/proposals")
    ? "proposals"
    : "internal";

  const runningLine =
    hydrated && data.cloudProfile
      ? data.cloudProfile.summaryPublic ||
        `${data.cloudProfile.provider || "SaaS"}, ${data.cloudProfile.region || "-"}, ${data.cloudProfile.environment || "Production"}`
      : "SaaS hosted in cloud provider of your choice.";

  return (
    <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-8 py-6">
        <PageHeader
          title="Configure"
          description={
            middleby
              ? "Analytics data sources, model, and shop integrations for Middleby."
              : "Data sources, models, and integrations in one place."
          }
          titleAction={<TourLaunchButton tourId="configure" label="Take a quick Configure tour" />}
        />
        {!isCloudSubRoute && (
          <div className="mt-6" data-tour-configure="tabs">
            <Tabs
              tabs={configureTabs}
              activeTab={activeTab}
              onChange={(val) => {
                const tab = configureTabs.find((t) => t.value === val);
                if (tab) navigate(tab.path);
              }}
            />
          </div>
        )}
        <div className={isCloudSubRoute ? "mt-2" : "mt-8"} data-tour-configure="body">{children}</div>

        {!isCloudSubRoute && (
          <div className="mt-10 rounded-xl border border-border-light bg-surface-secondary px-4 py-3 text-sm text-text-secondary" data-tour-configure="running-on">
            <span className="font-medium text-text-primary">Running on:</span> {runningLine}
            <span className="mx-2 text-text-muted">|</span>
            <Link to="/configure/cloud" className="font-semibold text-primary hover:text-primary-hover">
              View cloud details
            </Link>
            <span className="mx-2 text-text-muted">|</span>
            <Link to="/how-it-works" className="font-semibold text-primary hover:text-primary-hover">
              How Enterprise GPT works
            </Link>
          </div>
        )}
    </div>
  );
}
