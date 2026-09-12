// App router: lazy-loaded route components grouped by feature area.
import { lazy, Suspense } from "react";
import { Routes, Route, Link, Navigate, useLocation, useParams } from "react-router-dom";
import { useDemoProfile } from "./context/DemoProfileContext";
import { usePrototypeAuth } from "./context/PrototypeAuthContext";
import { kpiRegistryBasePath } from "./domain/middleby-analytics-builder";
import Layout from "./components/shared/Layout";
import ProfileRouteGuard from "./components/shared/ProfileRouteGuard";
import RequireAuth from "./components/shared/RequireAuth";
import ConfigureShell from "./components/configure/ConfigureShell";
import ConfigureBusinessHierarchy from "./components/configure/ConfigureBusinessHierarchy";
import ConfigureInternalSources from "./components/configure/ConfigureInternalSources";
import ConfigureInternalSourceForm from "./components/configure/ConfigureInternalSourceForm";
import ConfigureModels from "./components/configure/ConfigureModels";
import ConfigureModelForm from "./components/configure/ConfigureModelForm";
import ConfigureExternal from "./components/configure/ConfigureExternal";
import ConfigureExternalForm from "./components/configure/ConfigureExternalForm";
import ConfigureCloudSetup from "./components/configure/ConfigureCloudSetup";
import ConfigureCloudDetails from "./components/configure/ConfigureCloudDetails";

// ── Auth ────────────────────────────────────────────────────────────────────
const LoginPage = lazy(() => import("./components/LoginPage"));
const OnboardingModal = lazy(() => import("./components/OnboardingModal"));

// ── Playground ──────────────────────────────────────────────────────────────
const PlaygroundHomeAlt = lazy(() => import("./components/PlaygroundHomeAlt"));
const PlaygroundRouter = lazy(() => import("./components/PlaygroundRouter"));
const KpiDiscoveryPage = lazy(() => import("./components/kpi/KpiDiscoveryPage"));
const ConfigureSemanticLayer = lazy(() => import("./components/configure/ConfigureSemanticLayer"));
const UseCaseComposeScreen = lazy(() => import("./components/usecase/UseCaseComposeScreen"));
const UseCasePreflightScreen = lazy(() => import("./components/usecase/UseCasePreflightScreen"));
const UseCasePlanScreen = lazy(() => import("./components/usecase/UseCasePlanScreen"));
const UseCaseRehearseScreen = lazy(() => import("./components/usecase/UseCaseRehearseScreen"));
const UseCaseActivateScreen = lazy(() => import("./components/usecase/UseCaseActivateScreen"));
const DashboardsPage = lazy(() => import("./components/kpi/DashboardsPage"));
const DashboardView = lazy(() => import("./components/kpi/DashboardView"));
const PlaygroundLibrarySidebar = lazy(() => import("./components/PlaygroundLibrarySidebar"));
const PlaygroundInventoryDetail = lazy(() => import("./components/PlaygroundInventoryDetail"));
const PlaygroundConnectorsSidebar = lazy(() => import("./components/PlaygroundConnectorsSidebar"));
const PlaygroundActiveChat = lazy(() => import("./components/PlaygroundActiveChat"));
const FrequentQueries = lazy(() => import("./components/FrequentQueries"));
const PlaygroundConnectorsPanel = lazy(() => import("./components/PlaygroundConnectorsPanel"));
const PlaygroundBuild = lazy(() => import("./components/PlaygroundBuild"));
const PlaygroundStoreOps = lazy(() => import("./components/PlaygroundStoreOps"));
const PlaygroundBusinessAreaPage = lazy(() => import("./components/PlaygroundBusinessAreaPage"));

// ── Use Cases ───────────────────────────────────────────────────────────────
const UseCaseTemplates = lazy(() => import("./components/UseCaseTemplates"));
const UseCaseGallery = lazy(() => import("./components/UseCaseGallery"));
const UseCasesEmpty = lazy(() => import("./components/UseCasesEmpty"));
const UseCaseBuilderChat = lazy(() => import("./components/UseCaseBuilderChat"));
const UseCaseBuilderForm = lazy(() => import("./components/UseCaseBuilderForm"));
const UCBuilderStep1Welcome = lazy(() => import("./components/UCBuilderStep1Welcome"));
const UCBuilderStep2UserQuestion = lazy(() => import("./components/UCBuilderStep2UserQuestion"));
// Step 3 - Data Scope (new)
const UCBuilderStep3DataScope = lazy(() => import("./components/UCBuilderStep3DataScope"));
// Step 4 - AI Plan (was Step 3)
const UCBuilderStep3AIPlan = lazy(() => import("./components/UCBuilderStep3AIPlan"));
// Step 5 - Plan Breakdown (was Step 4)
const UCBuilderStep4PlanBreakdown = lazy(() => import("./components/UCBuilderStep4PlanBreakdown"));
// Step 6 - Orchestration (was Step 5)
const UCBuilderStep5Orchestration = lazy(() => import("./components/UCBuilderStep5Orchestration"));
// Step 7 - Pre-flight Check (new)
const UCBuilderStep7PreflightCheck = lazy(() => import("./components/UCBuilderStep7PreflightCheck"));
// Step 8 - Activation (was Step 10)
const UCBuilderStep10Completion = lazy(() => import("./components/UCBuilderStep10Completion"));
// Step 9 - Execution Summary (new - outside draft context)
const UCBuilderStep9ExecutionSummary = lazy(() => import("./components/UCBuilderStep9ExecutionSummary"));
const UseCaseActivationFlow = lazy(() => import("./components/UseCaseActivationFlow"));
const EnterpriseGptImpact = lazy(() => import("./components/EnterpriseGptImpact"));
const UseCaseLaunchSummary = lazy(() => import("./components/UseCaseLaunchSummary"));
const UseCaseDraftLayout = lazy(() => import("./components/shared/UseCaseDraftLayout"));

// ── Co-Pilot Decision Feed ────────────────────────────────────────────────────
const CopilotDecisionFeed = lazy(() => import("./components/CopilotDecisionFeed"));

// Use Case Impact Dashboard
const UseCaseImpactDashboard = lazy(() => import("./components/UseCaseImpactDashboard"));

// ── KPIs ────────────────────────────────────────────────────────────────────
const KpisHubLayout = lazy(() => import("./components/KpisHubLayout"));
const KpiRegistry = lazy(() => import("./components/KpiRegistry"));
const KpiDataGallery = lazy(() => import("./components/KpiDataGallery"));
const KpiMappings = lazy(() => import("./components/KpiMappings"));
const KpiHealthDashboard = lazy(() => import("./components/KpiHealthDashboard"));
const AddKpi = lazy(() => import("./components/AddKpi"));
const AddKpiSource = lazy(() => import("./components/AddKpiSource"));

// ── Processes ───────────────────────────────────────────────────────────────
const Processes = lazy(() => import("./components/Processes"));
const AddProcess = lazy(() => import("./components/AddProcess"));
const ProcessDetail = lazy(() => import("./components/ProcessDetail"));

// ── Hierarchy explainer ─────────────────────────────────────────────────────
const HierarchyExplainer = lazy(() => import("./components/HierarchyExplainer"));

// ── Business areas catalog ──────────────────────────────────────────────────
const BusinessAreas = lazy(() => import("./components/BusinessAreas"));

// ── Alerts ──────────────────────────────────────────────────────────────────
const Alerts = lazy(() => import("./components/Alerts"));

// ── Monitor ─────────────────────────────────────────────────────────────────
const Monitor = lazy(() => import("./components/Monitor"));

// ── Profile ─────────────────────────────────────────────────────────────────
const ProfilePage = lazy(() => import("./components/ProfilePage"));

// ── Admin ───────────────────────────────────────────────────────────────────
const AdminSeedData = lazy(() => import("./components/AdminSeedData"));

// ── How Enterprise GPT works (business taxonomy) ────────────────────────────
const HowItWorks = lazy(() => import("./components/HowItWorks"));

// ── Middleby analytics (profile-guarded) ────────────────────────────────────
const MiddlebyAnalyticsCatalog = lazy(() => import("./components/MiddlebyAnalyticsCatalog"));
const AnalyticsInsightSession = lazy(() => import("./components/AnalyticsInsightSession"));
const MiddlebyMonitorHub = lazy(() => import("./components/middleby/MiddlebyMonitorHub"));
const MiddlebyMonitorDashboard = lazy(() => import("./components/middleby/MiddlebyMonitorDashboard"));
const MiddlebyDecisionFeed = lazy(() => import("./components/middleby/MiddlebyDecisionFeed"));
const MiddlebyAutomationHub = lazy(() => import("./components/middleby/MiddlebyAutomationHub"));
const MiddlebyAutomationPlaybookPage = lazy(() => import("./components/middleby/MiddlebyAutomationPlaybookPage"));
const MiddlebyPortfolioPerformance = lazy(() => import("./components/middleby/MiddlebyPortfolioPerformance"));
const MiddlebyAnalyticsBuilderStep1 = lazy(() => import("./components/middleby/MiddlebyAnalyticsBuilderStep1"));
const MiddlebyAnalyticsBuilderStep2 = lazy(() => import("./components/middleby/MiddlebyAnalyticsBuilderStep2"));
const MiddlebyAnalyticsBuilderStep3 = lazy(() => import("./components/middleby/MiddlebyAnalyticsBuilderStep3"));
const MiddlebyAnalyticsBuilderStep4 = lazy(() => import("./components/middleby/MiddlebyAnalyticsBuilderStep4"));

// ── Gallery (dev only) ─────────────────────────────────────────────────────
const screens = [
  { path: "/login", name: "Login", group: "Auth" },
  { path: "/login-guest", name: "Login (Guest)", group: "Auth" },
  { path: "/playground", name: "Playground - Browse use-case cards", group: "Playground" },
  { path: "/playground/library", name: "Library Sidebar", group: "Playground" },
  { path: "/playground/area/inventory", name: "Playground: Business area (Inventory)", group: "Playground" },
  { path: "/playground/inventory", name: "Inventory Detail", group: "Playground" },
  { path: "/playground/onboarding", name: "Onboarding Modal", group: "Playground" },
  { path: "/playground/connectors", name: "Connectors Sidebar", group: "Playground" },
  { path: "/playground/chat", name: "Active Chat", group: "Playground" },
  { path: "/playground/connectors-panel", name: "Connectors Panel", group: "Playground" },
  { path: "/playground/build", name: "Build", group: "Playground" },
  { path: "/playground/store-ops", name: "Store Ops", group: "Playground" },
  { path: "/configure/internal-sources", name: "Configure: Internal Sources", group: "Configure" },
  { path: "/configure/models", name: "Configure: Models", group: "Configure" },
  { path: "/configure/external", name: "Configure: External", group: "Configure" },
  { path: "/configure/cloud", name: "Configure: Cloud (read-only)", group: "Configure" },
  { path: "/configure/cloud-setup", name: "Configure: Cloud Setup", group: "Configure" },
  { path: "/use-cases", name: "Use Cases (redirects to templates)", group: "Use Cases" },
  { path: "/use-cases/gallery", name: "Use Cases: Gallery", group: "Use Cases" },
  { path: "/use-cases/templates", name: "Use Cases: Pre defined", group: "Use Cases" },
  { path: "/use-cases/empty", name: "Empty State", group: "Use Cases" },
  { path: "/use-cases/builder-chat", name: "Builder Chat", group: "Use Cases" },
  { path: "/use-cases/builder-form", name: "Builder Form", group: "Use Cases" },
  { path: "/use-cases/builder/step-1", name: "Step 1: Basic Details", group: "Builder" },
  { path: "/use-cases/builder/step-2", name: "Step 2: User Question", group: "Builder" },
  { path: "/use-cases/builder/step-3", name: "Step 3: Data Scope", group: "Builder" },
  { path: "/use-cases/builder/step-4", name: "Step 4: AI Plan", group: "Builder" },
  { path: "/use-cases/builder/step-5", name: "Step 5: Plan Breakdown", group: "Builder" },
  { path: "/use-cases/builder/step-6", name: "Step 6: Orchestration", group: "Builder" },
  { path: "/use-cases/builder/step-7", name: "Step 7: Pre-flight Check", group: "Builder" },
  { path: "/use-cases/builder/step-8", name: "Step 8: Activation", group: "Builder" },
  { path: "/use-cases/builder/step-9", name: "Step 9: Execution Summary", group: "Builder" },
  { path: "/use-cases/launch-summary", name: "Launch Summary", group: "Use Cases" },
  { path: "/copilot/decisions", name: "Co-Pilot Decision Feed", group: "Use Cases" },
  { path: "/use-cases/dashboard", name: "Use Case Dashboard", group: "Use Cases" },
  { path: "/kpis/discover", name: "KPIs: What my data supports", group: "KPIs" },
  { path: "/kpis/mappings", name: "KPIs: Mappings", group: "KPIs" },
  { path: "/kpis/gallery", name: "KPIs: Gallery", group: "KPIs" },
  { path: "/kpis/dashboard", name: "KPIs: Dashboard", group: "KPIs" },
  { path: "/processes", name: "Processes", group: "Processes" },
  { path: "/business-areas", name: "Business Areas", group: "Hierarchy" },
  { path: "/hierarchy", name: "Hierarchy Explainer", group: "Hierarchy" },
];

function ScreenGallery() {
  const groups = {};
  screens.forEach((s) => {
    if (!groups[s.group]) groups[s.group] = [];
    groups[s.group].push(s);
  });

  return (
    <div className="min-h-screen bg-gradient-to-b from-transparent to-white p-8 dark:to-[var(--app-surface-page)]">
      <div className="mx-auto max-w-5xl">
        <h1 className="mb-2 text-3xl font-bold text-text-primary">EnterpriseGPT - Screen Gallery</h1>
        <p className="mb-8 text-text-secondary">{screens.length} screens from Figma</p>
        {Object.entries(groups).map(([group, items]) => (
          <div key={group} className="mb-8">
            <h2 className="mb-3 text-lg font-semibold text-text-primary">{group}</h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
              {items.map((s) => (
                <Link
                  key={s.path}
                  to={s.path}
                  className="flex items-center justify-center rounded-lg border border-border bg-surface-default px-4 py-3 text-center text-sm font-medium text-text-secondary shadow-sm transition hover:border-primary hover:text-primary hover:shadow-md"
                >
                  {s.name}
                </Link>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Loading() {
  return (
    <div className="flex h-screen items-center justify-center bg-[var(--app-surface-page)]">
      <div className="text-lg text-text-muted">Loading...</div>
    </div>
  );
}

function KpisIndexToRegistry() {
  const { search } = useLocation();
  return <Navigate to={`/kpis/registry${search}`} replace />;
}

function MiddlebyAutomationRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/analytics/automation${search}`} replace />;
}

function MiddlebyKpisIndexToRegistry() {
  const { search } = useLocation();
  return <Navigate to={`/kpis/registry${search}`} replace />;
}

function EditKpiRedirect() {
  const { categoryIndex, sectionIndex, kpiIndex } = useParams();
  const { profileId } = useDemoProfile();
  const base = kpiRegistryBasePath(profileId);
  return (
    <Navigate
      to={`${base}/registry?edit=${categoryIndex}.${sectionIndex}.${kpiIndex}`}
      replace
    />
  );
}

function RootRedirect() {
  const { ready, authEnabled, isAuthenticated } = usePrototypeAuth();
  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center bg-[var(--app-surface-page)] text-sm text-text-secondary">
        Loading…
      </div>
    );
  }
  if (authEnabled && !isAuthenticated) return <Navigate to="/login" replace />;
  return <Navigate to="/playground" replace />;
}

function CatchAllRedirect() {
  const { ready, authEnabled, isAuthenticated } = usePrototypeAuth();
  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center bg-[var(--app-surface-page)] text-sm text-text-secondary">
        Loading…
      </div>
    );
  }
  if (authEnabled && !isAuthenticated) return <Navigate to="/login" replace />;
  return <Navigate to="/playground" replace />;
}

/** Remount insight session when catalogId changes so title, question, and chart state stay in sync. */
function AnalyticsInsightSessionRoute() {
  const { catalogId } = useParams();
  return <AnalyticsInsightSession key={catalogId} />;
}

export default function App() {
  return (
    <div className="flex min-h-[100vh] min-h-dvh w-full flex-1 flex-col bg-[var(--app-surface-page)] text-text-primary">
      <Suspense fallback={<Loading />}>
        <Routes>
          {/* Auth - no layout (public when gate enabled) */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/login-guest" element={<Navigate to="/login" replace />} />

          <Route element={<RequireAuth />}>
            {/* Dev gallery */}
            <Route path="/gallery" element={<ScreenGallery />} />
            <Route path="/onboarding" element={<OnboardingModal />} />

            {/* App shell with persistent NavBar */}
            <Route element={<ProfileRouteGuard><Layout /></ProfileRouteGuard>}>
            {/* Playground */}
            <Route path="/playground" element={<PlaygroundRouter />} />
            <Route path="/playground/library" element={<PlaygroundLibrarySidebar />} />
            <Route path="/playground/area/:areaSlug" element={<PlaygroundBusinessAreaPage />} />
            <Route path="/playground/inventory" element={<PlaygroundInventoryDetail />} />
            <Route path="/playground/connectors" element={<PlaygroundConnectorsSidebar />} />
            <Route path="/playground/connectors-panel" element={<PlaygroundConnectorsPanel />} />
            <Route path="/playground/build" element={<PlaygroundBuild />} />
            <Route path="/playground/store-ops" element={<PlaygroundStoreOps />} />
            <Route path="/playground/chat" element={<PlaygroundActiveChat />} />
            <Route path="/playground/queries" element={<FrequentQueries />} />

            {/* Middleby analytics (blocked for default / GFI via ProfileRouteGuard) */}
            <Route path="/analytics" element={<MiddlebyAnalyticsCatalog />} />
            <Route path="/analytics/performance" element={<MiddlebyPortfolioPerformance />} />
            <Route path="/analytics/monitor" element={<MiddlebyMonitorHub />} />
            <Route path="/analytics/monitor/:monitorId" element={<MiddlebyMonitorDashboard />} />
            <Route path="/analytics/automation/queue" element={<MiddlebyDecisionFeed />} />
            <Route path="/analytics/automation/:patternId" element={<MiddlebyAutomationPlaybookPage />} />
            <Route path="/analytics/automation" element={<MiddlebyAutomationHub />} />
            <Route path="/analytics/decisions" element={<MiddlebyAutomationRedirect />} />
            {/* Middleby KPI routes are unified with the retail hub at /kpis/*.
                Legacy /analytics/kpis/* URLs redirect so old links keep working. */}
            <Route path="/analytics/kpis" element={<MiddlebyKpisIndexToRegistry />} />
            <Route path="/analytics/kpis/registry" element={<Navigate to="/kpis/registry" replace />} />
            <Route path="/analytics/kpis/new" element={<Navigate to="/kpis/new" replace />} />
            <Route element={<UseCaseDraftLayout />}>
              <Route path="/analytics/builder/step-1" element={<MiddlebyAnalyticsBuilderStep1 />} />
              <Route path="/analytics/builder/step-2" element={<MiddlebyAnalyticsBuilderStep2 />} />
              <Route path="/analytics/builder/step-3" element={<MiddlebyAnalyticsBuilderStep3 />} />
              <Route path="/analytics/builder/step-4" element={<MiddlebyAnalyticsBuilderStep4 />} />
            </Route>
            <Route path="/playground/insight/:catalogId" element={<AnalyticsInsightSessionRoute />} />

            {/* Configure (unified: internal sources, model, external integrations, cloud setup) */}
            <Route path="/configure" element={<ConfigureShell />}>
              <Route index element={<Navigate to="hierarchy" replace />} />
              <Route path="hierarchy" element={<ConfigureBusinessHierarchy />} />
              <Route path="internal-sources" element={<ConfigureInternalSources />} />
              <Route path="internal-sources/new" element={<ConfigureInternalSourceForm />} />
              <Route path="internal-sources/edit/:id" element={<ConfigureInternalSourceForm />} />
              <Route path="models" element={<ConfigureModels />} />
              <Route path="models/new" element={<ConfigureModelForm />} />
              <Route path="models/edit/:id" element={<ConfigureModelForm />} />
              <Route path="external" element={<ConfigureExternal />} />
              <Route path="external/new" element={<ConfigureExternalForm />} />
              <Route path="external/edit/:id" element={<ConfigureExternalForm />} />
              <Route path="domain-mapping" element={<KpiMappings />} />
              {/* Reached from a source card, not from a tab: the semantic layer belongs to the
                  source it was derived from, and a tab of its own would be empty until something
                  is connected. */}
              <Route path="semantic-layer" element={<ConfigureSemanticLayer />} />
              <Route path="cloud" element={<ConfigureCloudDetails />} />
              <Route path="cloud-setup" element={<ConfigureCloudSetup />} />
            </Route>

            <Route path="/integrations" element={<Navigate to="/configure/external" replace />} />
            <Route path="/integrations/retail-platforms" element={<Navigate to="/configure/external" replace />} />
            <Route path="/integrations/shopify" element={<Navigate to="/configure/external/new?provider=shopify" replace />} />

            {/* Use Cases */}
            <Route path="/use-cases" element={<Navigate to="/use-cases/templates" replace />} />
            <Route path="/use-cases/gallery" element={<UseCaseGallery />} />
            <Route path="/use-cases/templates" element={<UseCaseTemplates />} />
            <Route path="/use-cases/empty" element={<UseCasesEmpty />} />
            <Route path="/use-cases/builder-chat" element={<UseCaseBuilderChat />} />
            <Route path="/use-cases/builder-form" element={<UseCaseBuilderForm />} />
            <Route element={<UseCaseDraftLayout />}>
              <Route path="/use-cases/builder/step-1" element={<UCBuilderStep1Welcome />} />
              <Route path="/use-cases/builder/step-2" element={<UCBuilderStep2UserQuestion />} />
              {/* Step 2 of the 4-step builder: the spec review that replaces the old
                  AI-plan / plan-breakdown / orchestration steps. Parked on its own path while
                  the two flows exist side by side. */}
              {/* Six-step builder on the autonomy axis: compose (describe + spec, one screen)
                  -> preflight -> plan (how it will run, approved) -> rehearse -> activate.
                  Old entry paths redirect. */}
              <Route path="/use-cases/builder/compose" element={<UseCaseComposeScreen />} />
              <Route path="/use-cases/builder/describe" element={<Navigate to="/use-cases/builder/compose" replace />} />
              <Route path="/use-cases/builder/spec" element={<Navigate to="/use-cases/builder/compose" replace />} />
              <Route path="/use-cases/builder/preflight" element={<UseCasePreflightScreen />} />
              <Route path="/use-cases/builder/plan" element={<UseCasePlanScreen />} />
              <Route path="/use-cases/builder/rehearse" element={<UseCaseRehearseScreen />} />
              <Route path="/use-cases/builder/activate" element={<UseCaseActivateScreen />} />
              <Route path="/use-cases/builder/step-3" element={<UCBuilderStep3DataScope />} />
              <Route path="/use-cases/builder/step-4" element={<UCBuilderStep3AIPlan />} />
              <Route path="/use-cases/builder/step-5" element={<UCBuilderStep4PlanBreakdown />} />
              <Route path="/use-cases/builder/step-6" element={<UCBuilderStep5Orchestration />} />
              <Route path="/use-cases/builder/step-7" element={<UCBuilderStep7PreflightCheck />} />
              <Route path="/use-cases/builder/step-8" element={<UCBuilderStep10Completion />} />
            </Route>
            {/* Step 9 - Execution Summary: unified with the Activation flow. Renders the
                playbook-backed activation surface when the launched use case has a playbook;
                otherwise falls back to the Decision Cards summary. */}
            <Route path="/use-cases/builder/step-9" element={<UseCaseActivationFlow />} />
            {/* Activation flow for pre-existing use cases (from gallery cards, etc.) */}
            <Route path="/use-cases/activate/:catalogId" element={<UseCaseActivationFlow />} />
            <Route path="/use-cases/launch-summary" element={<UseCaseLaunchSummary />} />
            <Route path="/use-cases/dashboard" element={<UseCaseImpactDashboard />} />
            <Route path="/impact" element={<EnterpriseGptImpact />} />
            {/* Co-Pilot Decision Feed */}
            <Route path="/copilot/decisions" element={<CopilotDecisionFeed />} />

            {/* KPIs hub: Registry, Gallery, Dashboard */}
            <Route path="/kpis" element={<KpisHubLayout />}>
              {/* Discovery is the hub's front door: the first question is what the connected
                  data can measure, not which dataset backs metric #37. */}
              <Route index element={<Navigate to="/kpis/discover" replace />} />
              <Route path="discover" element={<KpiDiscoveryPage />} />
              <Route path="dashboards" element={<DashboardsPage />} />
              <Route path="dashboards/:dashboardId" element={<DashboardView />} />
              {/* Registry removed as a tab: discovery shows the same metrics with feasibility and
                  provenance, and the old screen contradicted itself (a "read-only" view carrying
                  Add and Import buttons). Kept as a redirect because Monitor, Business areas, the
                  hierarchy explainer, the tours and the Middleby profile all still link here. */}
              <Route path="registry" element={<Navigate to="/kpis/discover" replace />} />
              <Route path="mappings" element={<Navigate to="/configure/domain-mapping" replace />} />
              <Route path="gallery" element={<KpiDataGallery />} />
              <Route path="dashboard" element={<KpiHealthDashboard />} />
            </Route>
            <Route path="/kpis/new" element={<AddKpi />} />
            <Route path="/kpis/sources/new" element={<AddKpiSource />} />
            <Route
              path="/kpis/edit/:categoryIndex/:sectionIndex/:kpiIndex"
              element={<EditKpiRedirect />}
            />

            {/* Processes */}
            <Route path="/processes" element={<Processes />} />
            <Route path="/processes/new" element={<AddProcess />} />
            <Route path="/processes/:processId" element={<ProcessDetail />} />

            {/* Hierarchy explainer */}
            <Route path="/hierarchy" element={<HierarchyExplainer />} />

            {/* Business areas catalog */}
            <Route path="/business-areas" element={<BusinessAreas />} />

            {/* Alerts */}
            <Route path="/alerts" element={<Alerts />} />

            {/* Monitor */}
            <Route path="/monitor" element={<Monitor />} />

            {/* Profile */}
            <Route path="/profile" element={<ProfilePage />} />
            {/* Admin — unlinked from main nav. Reachable via profile menu. */}
            <Route path="/admin/seed-data" element={<AdminSeedData />} />
            {/* How Enterprise GPT works — reachable via profile menu + Configure footer. */}
            <Route path="/how-it-works" element={<HowItWorks />} />
            </Route>
          </Route>

          {/* Default redirect */}
          <Route path="/" element={<RootRedirect />} />
          <Route path="*" element={<CatchAllRedirect />} />
        </Routes>
      </Suspense>
    </div>
  );
}
