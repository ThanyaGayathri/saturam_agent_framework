// Use Case Templates - Browse and discover prebuilt use cases
// Figma node: 17624:312408
import { useMemo, useState } from "react";
import { clearSpecDraft } from "../domain/use-case-draft-spec";
import { useNavigate, useLocation } from "react-router-dom";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { useCaseActivationPath } from "../domain/automation-registry";
import { getKpiNamesForCatalogId } from "../domain/kpi-usecase-links";
import { FEASIBILITY, feasibilityForUseCase, feasibilitySummary } from "../domain/use-case-feasibility";
import {
  Plus,
  X,
  PenLine,
  ArrowRight,
  Crown,
  Cog,
  ScanLine,
  Compass,
  Wrench,
  Activity,
  Gauge,
} from "lucide-react";
import { loadUseCaseDraft } from "../storage/use-case-draft";
import { storageKeysForProfile } from "../storage/keys";
import { Button } from "./ui";
import { entryMatchesSearchQuery } from "../storage/use-cases-catalog";
import { TIER_LABELS } from "../domain/use-case-catalog-seed";
import { useDemoProfile } from "../context/DemoProfileContext";
import { isInRole, roleRank, ROLE_STYLE } from "../domain/featured-use-cases";
import { CatalogTagPill } from "./shared/catalogUseCaseTagPill";
import { UseCasePageNav } from "./shared/UseCasePageNav";
import TourLaunchButton from "../tours/TourLaunchButton";
import { CatalogSearchToolbar } from "./shared/CatalogSearchToolbar";
import { UseCaseTierFilterSelect } from "./shared/UseCaseTierFilterSelect";
import { EnterpriseProductMark } from "./shared/EnterpriseProductMark";
import {
  catalogPageShell,
  catalogPageInner,
  catalogHeaderRow,
  catalogHeaderText,
  catalogH1,
  catalogSubtitle,
  catalogPrimaryAction,
  catalogSectionBelowNav,
} from "./shared/useCaseCatalogPageStyles";

const ROLE_ICON = { executive: Crown, operations: Cog, analyst: ScanLine };

function resumeStepFromDraft(draft) {
  if (!draft || !draft.step1?.title?.trim()) return null;
  if ((draft.step5?.tasks ?? []).length > 0) return { path: "/use-cases/builder/step-7", label: "Pre-flight Check (Step 7)" };
  if ((draft.step4?.planItems ?? []).length > 0) return { path: "/use-cases/builder/step-5", label: "Plan Breakdown (Step 5)" };
  if ((draft.step3?.confirmedSources ?? []).some((s) => s.enabled)) return { path: "/use-cases/builder/step-4", label: "AI Generates Plan (Step 4)" };
  if (draft.step2?.primaryQuestion?.trim()) return { path: "/use-cases/builder/step-3", label: "Data Scope (Step 3)" };
  return { path: "/use-cases/builder/step-2", label: "User Question (Step 2)" };
}

const MAX_TAGS = 3;

/** Shared height for role + tier chips on template cards. */
const TEMPLATE_CARD_BADGE_STYLE = "min-h-[26px] items-center";

const TIER_BADGE_CLS = {
  strategic:
    "border border-amber-200/85 bg-amber-50 text-amber-900 dark:border-amber-500/35 dark:bg-amber-500/10 dark:text-amber-100",
  operational:
    "border border-emerald-200/85 bg-emerald-50 text-emerald-900 dark:border-emerald-500/35 dark:bg-emerald-500/12 dark:text-emerald-100",
  tactical:
    "border border-primary/25 bg-primary-light text-primary dark:border-primary/35 dark:bg-surface-secondary dark:text-primary",
};

const TIER_ICON = {
  strategic: Compass,
  operational: Wrench,
  tactical: Activity,
};

/** @param {{ useCase: object, featured?: boolean, isUserCreated?: boolean, roleStyle?: object, RoleIcon?: React.ElementType, onCardClick: () => void }} props */
function UseCaseCard({ useCase, featured = false, isUserCreated = false, roleStyle, RoleIcon, onCardClick }) {
  const items = useCase.items?.length ? useCase.items : [];
  const summary =
    (useCase.problemSignal && String(useCase.problemSignal).trim()) ||
    (items[0] ? String(items[0]) : "");
  const rawTags = useCase.tags?.length ? useCase.tags : useCase.businessArea ? [useCase.businessArea] : [];
  const tags = rawTags.slice(0, MAX_TAGS);
  const moreTags = Math.max(0, rawTags.length - MAX_TAGS);
  const tagSurface = isUserCreated ? "builder" : "template";
  const tierKey = useCase.tier;
  const TierGlyph = tierKey && TIER_ICON[tierKey] ? TIER_ICON[tierKey] : null;
  const kpiNames = getKpiNamesForCatalogId(useCase.id);
  // Seeded templates are the only ones scored: a user-built use case was authored against data
  // the author already had, so telling them it is "ready" says nothing.
  const feas = isUserCreated ? { state: "unknown" } : feasibilityForUseCase(useCase.id);
  const feasMeta = FEASIBILITY[feas.state];

  // Pre-defined (seed) templates: utility indigo-50 / indigo-100 surface
  // User-created: white + subtle purple gradient
  return (
    <div
      onClick={onCardClick}
      className={`group relative flex h-full min-h-0 cursor-pointer flex-col rounded-xl p-5 transition-all duration-200 hover:-translate-y-0.5 ${
        isUserCreated
          ? "border border-border bg-surface bg-[linear-gradient(45deg,_rgba(255,255,255,0)_-0.12%,_rgba(189,161,255,0.10)_56.37%,_rgba(105,65,198,0.10)_91.36%)] hover:shadow-[0_10px_24px_-10px_rgba(105,65,198,0.25)] dark:border-border dark:bg-gradient-to-br dark:from-surface-secondary dark:to-surface-tertiary dark:hover:shadow-[0_10px_24px_-10px_rgba(105,65,198,0.2)]"
          : "border border-primary/25 bg-primary-light hover:shadow-[0_10px_24px_-10px_rgba(105,65,198,0.25)] dark:border-primary/30 dark:bg-gradient-to-br dark:from-surface-secondary dark:to-surface-default dark:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)] dark:hover:shadow-[0_10px_24px_-10px_rgba(105,65,198,0.2)]"
      }`}
    >
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1">
        <div className="mb-2.5 flex flex-wrap items-center gap-2">
          {isUserCreated && useCase.status === "draft" && (
            <span className="inline-flex items-center rounded-md border border-amber-200/90 bg-amber-50/90 px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wide text-[#b45309]">
              Draft
            </span>
          )}
          {/* What this template would take to run HERE. Placed first because it decides whether
              the rest of the card is worth reading. */}
          {feas.state !== "unknown" && (
            <span
              title={
                feas.state === "ready"
                  ? "Every metric this moves is measurable from your connected data."
                  : `Missing: ${feas.missing.join(", ")}`
              }
              className={`inline-flex ${TEMPLATE_CARD_BADGE_STYLE} items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold leading-[1.2] ${feasMeta.cls}`}
            >
              {feas.state === "ready"
                ? "Ready"
                : feas.state === "mostly"
                ? `${feas.ready} of ${feas.total} metrics`
                : "Needs more data"}
            </span>
          )}
          {!isUserCreated && featured && roleStyle && RoleIcon && (
            <span
              className={`inline-flex max-w-full ${TEMPLATE_CARD_BADGE_STYLE} gap-1 rounded-full py-0.5 pl-0.5 pr-2.5 text-[11px] font-semibold leading-snug ${roleStyle.badgeCls}`}
            >
              <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-surface-default shadow-sm ring-1 ring-border/55 dark:bg-surface-secondary dark:ring-border-light/55">
                <RoleIcon size={12} strokeWidth={2.25} className={roleStyle.iconCls} />
              </span>
              <span className="min-w-0 pr-0.5 leading-[1.2]">{roleStyle.badge}</span>
            </span>
          )}
          {!isUserCreated && tierKey && TIER_BADGE_CLS[tierKey] && (
            <span
              className={`inline-flex ${TEMPLATE_CARD_BADGE_STYLE} gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase leading-[1.2] tracking-wide ${TIER_BADGE_CLS[tierKey]}`}
            >
              {TierGlyph ? <TierGlyph size={12} strokeWidth={2.25} className="shrink-0 opacity-90" /> : null}
              {TIER_LABELS[tierKey]}
            </span>
          )}
        </div>
        <h3 className="text-lg font-semibold leading-7 tracking-[-0.2px] text-text-primary">
          {useCase.title}
        </h3>
        {summary ? (
          <p className="mt-2.5 line-clamp-2 text-sm leading-6 text-text-secondary">{summary}</p>
        ) : null}
        {tags.length > 0 ? (
          <div className="mb-3 mt-3.5 flex flex-wrap items-start gap-2">
            {tags.map((tag, ti) => (
              <CatalogTagPill key={`${tag}-${ti}`} text={String(tag)} surface={tagSurface} />
            ))}
            {moreTags > 0 ? (
              <span className="text-xs font-medium text-text-muted">+{moreTags}</span>
            ) : null}
          </div>
        ) : null}
        {kpiNames.length > 0 ? (
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <span
              className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.05em] text-text-muted"
              title="KPIs this use case moves"
            >
              <Gauge className="h-3 w-3" strokeWidth={2} aria-hidden />
              Moves
            </span>
            {kpiNames.map((k) => (
              <span
                key={k}
                title={k}
                className="inline-flex max-w-full items-center gap-1 rounded-md border border-[#a9f0d1] bg-[#ecfdf3] px-1.5 py-0.5 text-[11px] font-medium text-[#067647] dark:border-[#067647]/35 dark:bg-[#067647]/10 dark:text-[#6ce9a6]"
              >
                <span className="truncate">{k}</span>
              </span>
            ))}
          </div>
        ) : null}
        </div>
        {isUserCreated ? (
          <div className="mt-auto border-t border-white/60 pt-3.5 dark:border-border">
            <div className="flex items-center gap-1.5 text-sm font-semibold text-primary opacity-80 transition group-hover:opacity-100">
              <ArrowRight size={14} strokeWidth={2.5} />
              {useCase.status === "draft" ? "Continue building" : "Open in builder"}
            </div>
          </div>
        ) : (
          <div className="mt-auto border-t border-primary/25 pt-3.5 dark:border-border">
            <EnterpriseProductMark />
          </div>
        )}
      </div>
    </div>
  );
}

export default function UseCaseTemplates() {
  const navigate = useNavigate();
  const location = useLocation();
  const { entries, hydrated } = useUseCaseCatalog();
  const { profileId, role } = useDemoProfile();
  const roleStyle = ROLE_STYLE[role] ?? ROLE_STYLE.executive;
  const RoleIcon = ROLE_ICON[role] ?? Crown;
  const [query, setQuery] = useState("");
  const [tierFilter, setTierFilter] = useState(null);

  const activeDraft = useMemo(() => {
    try {
      const key = storageKeysForProfile(profileId).USE_CASE_DRAFT;
      return loadUseCaseDraft(key);
    } catch { return null; }
  }, [location.pathname, profileId]);
  const draftResume = resumeStepFromDraft(activeDraft);
  const launchedSameTitle =
    !!activeDraft?.step1?.title?.trim() &&
    entries.some(
      (e) =>
        e.source === "user" &&
        e.status !== "draft" &&
        (e.title || "").trim().toLowerCase() === activeDraft.step1.title.trim().toLowerCase()
    );
  const hideDraftBanner =
    activeDraft?.builderSession?.hideTemplatesDraftBanner === true || launchedSameTitle;

  /** Pre-defined catalog only: seeded templates, not user-created entries (those are in the Use Case gallery). */
  const filteredCatalogUCs = useMemo(() => {
    let list = entries.filter((e) => e.source === "seed");
    const q = query.trim();
    if (q) list = list.filter((uc) => entryMatchesSearchQuery(uc, q));
    if (tierFilter) list = list.filter((uc) => uc.tier === tierFilter);
    if (!q) {
      // Feasibility first, then the existing role ranking inside each band. With 82 templates
      // and most of them blocked on any given workspace, an unsorted list buries the ones that
      // would work -- and sorting by relevance alone puts unbuildable ideas at the top.
      list.sort((a, b) => {
        const fa = FEASIBILITY[feasibilityForUseCase(a.id).state].rank;
        const fb = FEASIBILITY[feasibilityForUseCase(b.id).state].rank;
        if (fa !== fb) return fa - fb;
        const ra = roleRank(a.id, profileId, role);
        const rb = roleRank(b.id, profileId, role);
        if (ra !== -1 && rb !== -1) return ra - rb;
        if (ra !== -1) return -1;
        if (rb !== -1) return 1;
        return 0;
      });
    }
    return list;
  }, [entries, query, tierFilter, profileId, role]);

  const totalSeedCount = useMemo(
    () => (entries || []).filter((e) => e.source === "seed").length,
    [entries]
  );

  const hasSearch = query.trim().length > 0;
  const hasFilters = hasSearch || tierFilter;
  const totalFiltered = filteredCatalogUCs.length;
  const totalEntries = totalSeedCount;
  // Leads with what the workspace CAN do. The same facts framed the other way -- "29 blocked" --
  // reads as a broken product rather than a capable one, and nobody acts on it.
  const feasCounts = useMemo(
    () => feasibilitySummary((entries || []).filter((e) => e.source === "seed")),
    [entries]
  );

  const openTemplateUseCase = (uc) => {
    if (uc.source === "user") {
      const path = uc.status === "draft"
        ? (uc.resumePath || "/use-cases/builder/step-1").split("?")[0]
        : "/use-cases/builder/step-8";
      navigate(`${path}?reopenCatalogId=${encodeURIComponent(uc.id)}`);
    } else {
      navigate(useCaseActivationPath(uc.id));
    }
  };

  if (!hydrated) {
    return (
      <div className={catalogPageShell}>
        <div className={catalogPageInner}>
          <p className="text-base leading-6 text-text-secondary">Loading templates…</p>
        </div>
      </div>
    );
  }

  return (
    <div className={catalogPageShell}>
      <div className={catalogPageInner}>
        <div className={catalogHeaderRow}>
          <div className={catalogHeaderText}>
            <div className="flex items-center gap-3">
              <h1 className={catalogH1}>Pre defined Use Case</h1>
              <TourLaunchButton tourId="use-case-templates" label="Take a quick Pre-defined Use Cases tour" />
            </div>
            <p className={catalogSubtitle}>
              Pre-built use case templates you can launch. After launch, active runs appear under User built use cases.
            </p>
          </div>
          <Button
            size="lg"
            onClick={() => { clearSpecDraft(); navigate("/use-cases/builder/describe"); }}
            className={`${catalogPrimaryAction} gap-1.5`}
            data-tour-uc="create"
          >
            <Plus size={18} strokeWidth={2.5} />
            Create Use Case
          </Button>
        </div>

        <UseCasePageNav current="templates" />

        <div className={`${catalogSectionBelowNav} mb-4`}>
          {/* In-progress draft card */}
          {draftResume && !hideDraftBanner && (
            <div className="relative mb-6 overflow-hidden rounded-xl border border-primary/30 bg-gradient-to-br from-surface to-primary-light p-5 shadow-sm dark:from-surface-secondary dark:to-primary/20">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-3 min-w-0">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-light dark:bg-primary/20 text-primary">
                    <PenLine size={18} />
                  </div>
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.4px] text-primary">
                      Draft in progress
                    </p>
                    <p className="mt-0.5 truncate text-[15px] font-semibold text-text-primary">
                      {activeDraft.step1.title || "Untitled use case"}
                    </p>
                    <p className="mt-0.5 text-[13px] text-text-secondary">
                      Resume at: <span className="font-medium text-text-tertiary">{draftResume.label}</span>
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => navigate(draftResume.path)}
                  className="flex shrink-0 items-center gap-1.5 self-start rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white transition hover:bg-primary-hover sm:self-auto"
                >
                  Continue
                  <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}

          {/* Search (left) + tier filter dropdown (right) */}
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 sm:max-w-md sm:flex-1">
              <CatalogSearchToolbar
                value={query}
                onValueChange={setQuery}
                placeholder="Search templates"
                className="!w-full sm:!w-full"
              />
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2 sm:shrink-0">
              <UseCaseTierFilterSelect
                id="uc-templates-tier"
                value={tierFilter}
                onValueChange={setTierFilter}
                labelText="Filter catalog by tier"
              />
              {hasFilters ? (
                <button
                  type="button"
                  onClick={() => { setQuery(""); setTierFilter(null); }}
                  className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-2 text-xs font-semibold text-primary transition hover:bg-[#f4f0ff]"
                >
                  <X size={12} strokeWidth={2.5} />
                  Clear
                </button>
              ) : null}
            </div>
          </div>

          <p className="mb-4 text-sm leading-6 text-text-secondary">
            {totalFiltered === 0 && hasFilters ? (
              <>No templates match your filters.</>
            ) : totalFiltered === 0 ? (
              "No pre-built templates in the catalog."
            ) : hasFilters ? (
              <>
                <span className="font-semibold tabular-nums text-text-primary">{totalFiltered}</span> of{" "}
                <span className="font-semibold tabular-nums text-text-primary">{totalEntries}</span> template
                {totalEntries === 1 ? "" : "s"}
              </>
            ) : (
              <>
                <span className="font-semibold tabular-nums text-text-primary">{feasCounts.ready}</span> of{" "}
                <span className="font-semibold tabular-nums text-text-primary">{totalFiltered}</span> template
                {totalFiltered === 1 ? "" : "s"} ready with your connected data
                {feasCounts.mostly > 0 ? (
                  <span className="text-text-muted"> · {feasCounts.mostly} need one more metric</span>
                ) : null}
              </>
            )}
          </p>

          {filteredCatalogUCs.length > 0 ? (
            <div
              className="grid grid-cols-1 items-stretch gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
              data-tour-uc="cards"
            >
              {filteredCatalogUCs.map((uc) => (
                <UseCaseCard
                  key={uc.id}
                  useCase={uc}
                  featured={isInRole(uc.id, profileId, role)}
                  isUserCreated={false}
                  roleStyle={roleStyle}
                  RoleIcon={RoleIcon}
                  onCardClick={() => openTemplateUseCase(uc)}
                />
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
