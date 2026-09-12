import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { BarChart3, ClipboardCheck as ClipboardCheckIcon, ExternalLink, Info, Pencil, Sparkles } from "lucide-react";
import Breadcrumb from "./shared/Breadcrumb";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getPlaybooksForCatalogId, registerCatalogEntry } from "../domain/automation-registry";
import { getSeedDecisionCardsForCatalog } from "../domain/decision-cards-catalog";
import { getBuilderEnterpriseDatasetIds } from "../storage/use-case-draft";
import { COPILOT_DECISIONS_KEY, USE_CASE_LAUNCH_SUMMARY_KEY } from "../storage/keys";

/** Maps businessArea labels used on catalog entries to the Builder's category
 *  slug so we can pull the same pre-seeded defaults the wizard would use. */
const BUSINESS_AREA_TO_CATEGORY = {
  Inventory: "inventory",
  Orders: "orders",
  Merchandise: "merchandise",
  "Store Ops": "store-ops",
  "E-Com": "ecom",
  POS: "pos",
  Support: "support",
  Pricing: "pricing",
  Finance: "finance",
  Workforce: "workforce",
};
import MiddlebyAutomationFlowAutomateStep from "./middleby/MiddlebyAutomationFlowAutomateStep";
import UCBuilderStep9ExecutionSummary from "./UCBuilderStep9ExecutionSummary";

/**
 * Unified activation surface for a use case.
 *
 * Reached from three places:
 *   1. Clicking a pre-existing use case card in the gallery
 *      (route: /use-cases/activate/:catalogId)
 *   2. Completing the Builder wizard and clicking Launch
 *      (route: /use-cases/builder/step-9, resolves the launched use case)
 *   3. Deep links from the Impact Dashboard or Decision Feed
 *
 * Renders one of two flows depending on whether a playbook is registered
 * for this catalog id:
 *
 *   Playbook-backed use cases (R1, G1, A1-A7):
 *     - Reuses MiddlebyAutomationFlowAutomateStep, which handles
 *       Prepare -> Review -> Approve -> Delivered end-to-end with the
 *       real playbook data.
 *     - Adds a Step 9-style header and a "View Impact Dashboard" CTA
 *       at the bottom once the run has been delivered.
 *
 *   Non-playbook use cases (majority of the catalog today):
 *     - Falls back to the existing Step 9 Executive Summary +
 *       Decision Cards experience. This preserves the current UX for
 *       use cases that do not yet have an automation configured.
 */
export default function UseCaseActivationFlow({ catalogIdProp = null }) {
  const navigate = useNavigate();
  const routeParams = useParams();
  // On the Step 9 route the catalog id is not in the URL - it comes from the
  // launch summary that Step 8 wrote to sessionStorage. Read it once on mount.
  const [launchedCatalogId, setLaunchedCatalogId] = useState(null);
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(USE_CASE_LAUNCH_SUMMARY_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed?.id) setLaunchedCatalogId(parsed.id);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const catalogId = catalogIdProp || routeParams.catalogId || launchedCatalogId || null;

  const { entries } = useUseCaseCatalog();
  const catalogEntry = useMemo(
    () => (catalogId ? (entries || []).find((e) => e.id === catalogId) ?? null : null),
    [entries, catalogId]
  );

  // Register the live catalog entry (with the user's draftSnapshot) so the
  // playbook generator uses their configured plan items / tasks instead of
  // the generic category defaults. Must run before playbook lookup.
  const playbooks = useMemo(() => {
    if (!catalogId) return [];
    if (catalogEntry) registerCatalogEntry(catalogEntry);
    return getPlaybooksForCatalogId(catalogId);
  }, [catalogId, catalogEntry]);
  const playbook = playbooks[0] ?? null;

  const title = catalogEntry?.title || catalogId || "Use case";

  // Non-playbook path:
  //   - If the entry has been activated (walked through the Builder and
  //     launched), show a "configured and running" state.
  //   - Else if the URL has an explicit :catalogId, show the "not yet
  //     configured" placeholder.
  //   - Else (Builder Launch route /use-cases/builder/step-9), fall through
  //     to the Step 9 Executive Summary + Decision Cards experience.
  // A use case counts as "activated" only when the user has walked through
  // the Builder wizard and launched it. Seed entries default status to
  // "active" in normalizeEntry (which is misleading), so we require a
  // draftSnapshot too - only launched entries write one.
  const isActivated = Boolean(
    catalogEntry?.draftSnapshot && catalogEntry?.status !== "draft"
  );

  // The workflow view (Prepare -> Review -> Approve -> Delivered) only makes
  // sense when there is real content to show. Explicit playbooks (A/R/G)
  // always qualify because they have hand-authored content. Auto-generated
  // playbooks only qualify when the user has actually configured the use
  // case (draftSnapshot present) - otherwise the flow shows generic
  // placeholder text disconnected from anything the user did.
  const playbookIsGenerated = Boolean(playbook?.id?.startsWith("AUTO-"));
  const showWorkflowView = Boolean(playbook) && (!playbookIsGenerated || isActivated);

  // Count decision cards linked to this use case in localStorage. If the
  // use case is activated and has seed cards defined but nothing is in
  // storage yet (activated before persistence merge landed, or Middleby
  // profile bypasses the Builder), auto-seed the storage so the Review
  // Decisions surface works consistently.
  const [decisionCount, setDecisionCount] = useState(0);
  useEffect(() => {
    if (!catalogId) return;
    try {
      const raw = localStorage.getItem(COPILOT_DECISIONS_KEY);
      const all = raw ? JSON.parse(raw) : [];
      const existingForThis = all.filter((c) => c.useCaseId === catalogId);
      if (existingForThis.length === 0 && isActivated) {
        const seeded = getSeedDecisionCardsForCatalog(catalogId, title);
        if (seeded.length > 0) {
          const merged = [...all, ...seeded];
          localStorage.setItem(COPILOT_DECISIONS_KEY, JSON.stringify(merged));
          setDecisionCount(seeded.length);
          return;
        }
      }
      setDecisionCount(existingForThis.length);
    } catch {
      /* ignore */
    }
  }, [catalogId, isActivated, title]);

  // Fall back to the status card view whenever the workflow view is not
  // eligible (no explicit playbook AND user has not yet configured it).
  if (!showWorkflowView) {
    if (routeParams.catalogId) {
      // Draft snapshot may carry configuration detail we can surface.
      const snap = catalogEntry?.draftSnapshot || null;

      // Pre-seeded defaults derived from the catalog entry alone. These are
      // what the Builder would fill in automatically on Steps 1 / 3 / 5 / 8
      // before the user changes anything. Showing them (instead of "-")
      // makes the "Not configured yet" card feel less empty and gives users
      // a preview of what the workflow will look like.
      const seedCategory =
        BUSINESS_AREA_TO_CATEGORY[catalogEntry?.businessArea] || "default";
      const suggestedSourceCount = getBuilderEnterpriseDatasetIds(seedCategory).length;
      const DEFAULT_TASK_COUNT = 5; // matches DEFAULT_AI_PLAN_ITEMS in the Builder

      const scheduleLabel = (() => {
        const s = snap?.step8?.schedule;
        if (s === "daily") return "Runs daily";
        if (s === "weekly") return "Runs weekly";
        if (s === "once") return "Runs on demand";
        return isActivated ? null : "Weekly (default)";
      })();
      const guardrail = snap?.step8?.guardrailAmount;
      const autonomy = snap?.step1?.autonomyLevel === "copilot"
        ? "Co-Pilot"
        : snap?.step1?.autonomyLevel === "autopilot"
        ? "Autopilot"
        : isActivated ? "Autopilot" : "Co-Pilot (default)";
      const sourceCount = (snap?.step3?.confirmedSources || []).filter((s) => s.enabled).length
        || (isActivated ? 0 : suggestedSourceCount);
      const taskCount = (snap?.step5?.tasks || []).length
        || (isActivated ? null : DEFAULT_TASK_COUNT);

      return (
        <div className="mx-auto max-w-3xl px-6 py-10">
          <Breadcrumb
            items={[
              { label: "Use Cases", to: "/use-cases" },
              { label: title },
            ]}
          />
          <header className="mt-4 mb-6 flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-primary">
                  Use case
                </p>
                {/* Same status pill treatment as playbook pages. Only the
                    colour and copy differ. */}
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
                    isActivated
                      ? "bg-[#ecfdf3] text-[#067647]"
                      : "bg-[#fffaeb] text-[#b54708]"
                  }`}
                >
                  <span
                    className={`inline-block h-1.5 w-1.5 rounded-full ${
                      isActivated ? "bg-[#067647]" : "bg-[#b54708]"
                    }`}
                  />
                  {isActivated ? "Configured and running" : "Not configured yet"}
                </span>
              </div>
              <h1 className="mt-2 text-2xl font-semibold tracking-tight text-text-primary">
                {title}
              </h1>
              {catalogEntry?.items?.length ? (
                <p className="mt-2 text-sm text-text-secondary">{catalogEntry.items[0]}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() =>
                navigate(`/use-cases/builder/step-1?reopenCatalogId=${encodeURIComponent(catalogId)}`)
              }
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
              Edit configuration
            </button>
          </header>

          {/* Configuration summary. For activated use cases, shows the actual
              schedule / autonomy / sources. For not-configured use cases,
              shows the same shape but empty-state prompting the user to
              Configure in Builder. */}
          <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-semibold text-text-primary">
                {isActivated ? "Automation configuration" : "Automation not configured yet"}
              </h2>
              <p className="text-xs text-text-muted">
                {isActivated
                  ? "Runs on schedule. Nothing changes until the next scheduled run or a manual trigger."
                  : "Configure in Builder to activate the analyze -> monitor -> approve -> deliver flow."}
              </p>
            </div>

            {/* Tiles show the pre-seeded Builder defaults when not activated
                (so the card is never blank) and the user's actual values
                once activated. */}
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-border bg-surface-secondary/40 px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                  Autonomy
                </p>
                <p className="mt-0.5 text-sm font-semibold text-text-primary">{autonomy}</p>
              </div>
              <div className="rounded-lg border border-border bg-surface-secondary/40 px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                  Schedule
                </p>
                <p className="mt-0.5 text-sm font-semibold text-text-primary">
                  {scheduleLabel ? scheduleLabel.replace(/^Runs /, "") : "-"}
                </p>
              </div>
              <div className="rounded-lg border border-border bg-surface-secondary/40 px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                  Data sources
                </p>
                <p className="mt-0.5 text-sm font-semibold text-text-primary">
                  {sourceCount > 0 ? sourceCount : "-"}
                </p>
              </div>
              <div className="rounded-lg border border-border bg-surface-secondary/40 px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                  Agent tasks
                </p>
                <p className="mt-0.5 text-sm font-semibold text-text-primary">
                  {taskCount ? taskCount : "-"}
                </p>
              </div>
            </div>
            {!isActivated ? (
              <p className="mt-3 text-xs text-text-muted">
                Values shown are pre-seeded defaults from the Builder. Open Configure to review and
                edit before activating.
              </p>
            ) : null}

            <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
              {isActivated ? (
                <Link
                  to={`/use-cases/dashboard?catalogId=${encodeURIComponent(catalogId)}`}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white hover:bg-primary-hover"
                >
                  <BarChart3 className="h-3.5 w-3.5" aria-hidden />
                  View Impact Dashboard
                </Link>
              ) : (
                <button
                  type="button"
                  onClick={() =>
                    navigate(`/use-cases/builder/step-1?reopenCatalogId=${encodeURIComponent(catalogId)}`)
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white hover:bg-primary-hover"
                >
                  <Sparkles className="h-3.5 w-3.5" aria-hidden />
                  Configure in Builder
                </button>
              )}
              {isActivated && decisionCount > 0 ? (
                <Link
                  to={`/copilot/decisions?useCaseId=${encodeURIComponent(catalogId)}`}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-primary hover:bg-surface-secondary"
                >
                  <ClipboardCheckIcon className="h-3.5 w-3.5" aria-hidden />
                  Review Decisions ({decisionCount})
                </Link>
              ) : null}
              {/* "View Impact Dashboard" removed from the Not-configured state
                  - nothing meaningful to view before activation. Configured
                  state still has it as the primary CTA above.
                  "View in Monitor" removed - only render when a monitor is
                  actually mapped for this catalog id. TODO: reintroduce
                  conditionally via getMonitorForCatalogId() when the retail /
                  GFI monitor manifest lists this catalogId. */}
            </div>
          </section>

          <div className="mt-8 border-t border-border pt-6">
            <Link
              to="/use-cases"
              className="text-sm font-medium text-text-muted hover:text-text-primary"
            >
              Back to Use Cases
            </Link>
          </div>
        </div>
      );
    }
    // Builder Launch route - keep the existing Step 9 behaviour
    return <UCBuilderStep9ExecutionSummary />;
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Breadcrumb
        items={[
          { label: "Use Cases", to: "/use-cases" },
          { label: title },
        ]}
      />

      <header className="mt-4 mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-primary">
              Use case
            </p>
            {/* Same status pill treatment as non-playbook pages so every
                activation surface shares one visual identity. */}
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[#ecfdf3] px-2.5 py-0.5 text-[11px] font-semibold text-[#067647]">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#067647]" />
              Configured and running
            </span>
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-text-primary">
            {title}
          </h1>
          <p className="mt-2 text-sm text-text-secondary">
            Enterprise GPT runs the discovery, prepares the actions, and waits for your
            approval before anything is sent.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() =>
              navigate(
                catalogId
                  ? `/use-cases/builder/step-1?reopenCatalogId=${encodeURIComponent(catalogId)}`
                  : "/use-cases/builder/step-1"
              )
            }
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
          >
            <Pencil className="h-3.5 w-3.5" aria-hidden />
            Edit configuration
          </button>
        </div>
      </header>

      <MiddlebyAutomationFlowAutomateStep
        patternId={playbook.id}
        signalRecap={playbook.trigger}
        hideStepOrdinal
        compactInsight
      />

      <div className="mt-8 flex flex-wrap items-center gap-3 border-t border-border pt-6">
        <Link
          to={`/use-cases/dashboard?catalogId=${encodeURIComponent(catalogId)}`}
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-4 py-2.5 text-sm font-semibold text-text-primary hover:bg-surface-secondary"
        >
          <BarChart3 className="h-4 w-4" aria-hidden />
          View Impact Dashboard
        </Link>
        {/* "View in Monitor" removed - /monitor is a global business-monitor
            page, not scoped to this use case, so the link doesn't add value
            from a specific use case's activation page. Monitor is still
            reachable from the top nav. */}
        <Link
          to="/use-cases"
          className="ml-auto text-sm font-medium text-text-muted hover:text-text-primary"
        >
          Back to Use Cases
        </Link>
      </div>
    </div>
  );
}
