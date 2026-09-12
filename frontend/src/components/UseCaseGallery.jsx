import { useMemo, useState } from "react";
import { clearSpecDraft } from "../domain/use-case-draft-spec";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Plus,
  PenLine,
  ArrowRight,
  Blocks,
  X,
} from "lucide-react";
import { loadUseCaseDraft } from "../storage/use-case-draft";
import { useCaseActivationPath } from "../domain/automation-registry";
import { getKpiNamesForCatalogId } from "../domain/kpi-usecase-links";
import { storageKeysForProfile } from "../storage/keys";
import { useDemoProfile } from "../context/DemoProfileContext";
import { roleRank } from "../domain/featured-use-cases";
import { Button } from "./ui";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { useProcesses } from "../hooks/useProcesses";
import { formatProcessFieldForUi } from "../storage/processes";
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
import { UseCasePageNav } from "./shared/UseCasePageNav";
import { GalleryUseCaseCard, cardVariant } from "./shared/GalleryUseCaseCard";
import { CatalogSearchToolbar } from "./shared/CatalogSearchToolbar";
import { UseCaseTierFilterSelect } from "./shared/UseCaseTierFilterSelect";
import { entryMatchesSearchQuery } from "../storage/use-cases-catalog";
import TourLaunchButton from "../tours/TourLaunchButton";

export default function UseCaseGallery() {
  const navigate = useNavigate();
  const location = useLocation();
  const { entries, hydrated, softDeleteCustomUseCase, setData } = useUseCaseCatalog();
  const { processes } = useProcesses();
  const { profileId, role } = useDemoProfile();

  /** Active, deployed use cases only (in use, not drafts). Pre-built templates live under Pre defined Use Case. */
  const activeUserEntriesSorted = useMemo(() => {
    const list = [...(entries || [])].filter((e) => e.source === "user" && e.status === "active");
    list.sort((a, b) => {
      const ra = roleRank(a.id, profileId, role);
      const rb = roleRank(b.id, profileId, role);
      if (ra !== -1 && rb !== -1) return ra - rb;
      if (ra !== -1) return -1;
      if (rb !== -1) return 1;
      const ga = a.galleryOrder ?? Number.MAX_SAFE_INTEGER;
      const gb = b.galleryOrder ?? Number.MAX_SAFE_INTEGER;
      if (ga !== gb) return ga - gb;
      return String(a.updatedAt || "").localeCompare(String(b.updatedAt || ""));
    });
    return list;
  }, [entries, profileId, role]);

  const [tierFilter, setTierFilter] = useState(null);
  const [search, setSearch] = useState("");

  const searchFiltered = useMemo(() => {
    let list = activeUserEntriesSorted;
    const q = search.trim();
    if (q) list = list.filter((e) => entryMatchesSearchQuery(e, q));
    if (tierFilter) list = list.filter((e) => e.tier === tierFilter);
    return list;
  }, [activeUserEntriesSorted, search, tierFilter]);

  /** Operational first, then customer-facing. */
  const yourUseCasesOrdered = useMemo(() => {
    const operational = [];
    const customer = [];
    for (const e of searchFiltered) {
      if (cardVariant(e.actionType) === "operational") operational.push(e);
      else customer.push(e);
    }
    return [...operational, ...customer];
  }, [searchFiltered]);

  const activeDraft = useMemo(() => {
    try {
      const key = storageKeysForProfile(profileId).USE_CASE_DRAFT;
      return loadUseCaseDraft(key);
    } catch { return null; }
  }, [location.pathname, profileId]);
  const draftTitle = activeDraft?.step1?.title?.trim();
  const draftResumePath = useMemo(() => {
    if (!draftTitle) return null;
    const d = activeDraft;
    if ((d.step5?.tasks ?? []).length > 0) return { path: "/use-cases/builder/step-7", label: "Pre-flight Check (Step 7)" };
    if ((d.step4?.planItems ?? []).length > 0) return { path: "/use-cases/builder/step-5", label: "Plan Breakdown (Step 5)" };
    if ((d.step3?.confirmedSources ?? []).some((s) => s.enabled)) return { path: "/use-cases/builder/step-4", label: "AI Generates Plan (Step 4)" };
    if (d.step2?.primaryQuestion?.trim()) return { path: "/use-cases/builder/step-3", label: "Data Scope (Step 3)" };
    return { path: "/use-cases/builder/step-2", label: "User Question (Step 2)" };
  }, [activeDraft, draftTitle]);

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

  // Four steps now, starting from the question rather than a title. `step-1` stays routable for
  // resume and reopen-catalog journeys, which this flow does not cover.
  const openBuilder = () => { clearSpecDraft(); navigate("/use-cases/builder/describe"); };

  const handleMenuClick = async (useCase) => {
    if (useCase.source === "seed" && !useCase.__supabaseId) {
      window.alert(
        "Pre-defined use cases can't be deleted. Toggle inactive from the activation page if you don't want to run it."
      );
      return;
    }
    if (!window.confirm(`Delete "${useCase.title}"?`)) return;
    if (useCase.__supabaseId) {
      await softDeleteCustomUseCase(useCase.__supabaseId);
      return;
    }
    // Legacy localStorage-only user entry (added before Supabase was wired).
    setData((prev) => ({
      ...prev,
      entries: (prev.entries || []).filter((e) => e.id !== useCase.id),
    }));
  };

  const openGalleryUseCase = (useCase) => {
    if (useCase.source === "user") {
      const path = useCase.status === "draft"
        ? (useCase.resumePath || "/use-cases/builder/step-1").split("?")[0]
        : "/use-cases/builder/step-8";
      navigate(`${path}?reopenCatalogId=${encodeURIComponent(useCase.id)}`);
    } else {
      navigate(useCaseActivationPath(useCase.id));
    }
  };

  const hasAnyVisible = yourUseCasesOrdered.length > 0;
  const hasActiveFilters =
    search.trim().length > 0 || !!tierFilter;

  if (!hydrated) {
    return (
      <div className={catalogPageShell}>
        <div className={catalogPageInner}>
          <p className="text-base leading-6 text-text-secondary">Loading use cases…</p>
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
              <h1 className={catalogH1}>User built use cases</h1>
              <TourLaunchButton tourId="use-case-gallery" label="Take a quick User built use cases tour" />
            </div>
            <p className={catalogSubtitle}>
              Use cases you have launched, activated, and are monitoring. Open a card to go to the dashboard or manage the run.
            </p>
          </div>
          <Button
            size="lg"
            onClick={openBuilder}
            className={`${catalogPrimaryAction} gap-1.5`}
            data-tour-uc="create"
          >
            <Plus size={18} strokeWidth={2.5} />
            Create Use Case
          </Button>
        </div>

        <div data-tour-uc="tabs">
          <UseCasePageNav current="gallery" />
        </div>

        <div className={`${catalogSectionBelowNav} space-y-8`}>
          {/* In-progress draft card */}
          {draftResumePath && !hideDraftBanner && (
            <div className="relative overflow-hidden rounded-xl border border-primary/30 bg-gradient-to-br from-surface-default to-primary-light/80 p-5 shadow-sm dark:border-border dark:from-surface-secondary dark:to-surface-default dark:bg-none dark:ring-1 dark:ring-primary/25">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-3 min-w-0">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-light text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30">
                    <PenLine size={18} />
                  </div>
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.4px] text-primary">
                      Draft in progress
                    </p>
                    <p className="mt-0.5 truncate text-[15px] font-semibold text-text-primary">
                      {draftTitle}
                    </p>
                    <p className="mt-0.5 text-[13px] text-text-secondary">
                      Resume at: <span className="font-medium text-text-tertiary">{draftResumePath.label}</span>
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => navigate(draftResumePath.path)}
                  className="flex shrink-0 items-center gap-1.5 self-start rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white transition hover:bg-primary-hover sm:self-auto"
                >
                  Continue
                  <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}

          {/* Search (left) + tier filter (right) */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 sm:max-w-md sm:flex-1">
              <CatalogSearchToolbar
                value={search}
                onValueChange={setSearch}
                placeholder="Search use cases"
                className="!w-full sm:!w-full"
              />
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2 sm:shrink-0">
              <UseCaseTierFilterSelect
                id="uc-gallery-tier"
                value={tierFilter}
                onValueChange={setTierFilter}
                labelText="Filter use cases by tier"
              />
              {hasActiveFilters ? (
                <button
                  type="button"
                  onClick={() => {
                    setTierFilter(null);
                    setSearch("");
                  }}
                  className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-2 text-xs font-semibold text-primary transition hover:bg-[#f4f0ff]"
                >
                  <X size={12} strokeWidth={2.5} />
                  Clear
                </button>
              ) : null}
            </div>
          </div>

          {/* Empty: no matches vs nothing at all */}
          {!hasAnyVisible && hasActiveFilters ? (
            <div className="rounded-2xl border border-dashed border-primary/30 bg-surface/60 px-6 py-12 text-center dark:border-border dark:bg-surface-secondary/50 dark:ring-1 dark:ring-primary/15">
              <p className="text-base font-semibold text-text-primary">No use cases match your search or filters</p>
              <p className="mt-1 text-sm text-text-secondary">Try different keywords, clear the tier, or reset search.</p>
              <div className="mt-4 flex justify-center">
                <button
                  type="button"
                  onClick={() => {
                    setTierFilter(null);
                    setSearch("");
                  }}
                  className="rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-tertiary hover:bg-surface-secondary"
                >
                  Clear search and filters
                </button>
              </div>
            </div>
          ) : null}
          {!hasAnyVisible && !hasActiveFilters ? (
            <div className="rounded-2xl border border-dashed border-primary/30 bg-surface/60 px-6 py-12 text-center dark:border-border dark:bg-surface-secondary/50 dark:ring-1 dark:ring-primary/15">
              <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-primary-light text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30">
                <Blocks size={20} />
              </div>
              <p className="mt-3 text-base font-semibold text-text-primary">No active use cases yet</p>
              <p className="mt-1 text-sm text-text-secondary">
                Launch a pre-built template from the catalog, or build your own, then return here to monitor what is live.
              </p>
              <div className="mt-4 flex items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={() => navigate("/use-cases/templates")}
                  className="rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-tertiary hover:bg-surface-secondary"
                >
                  Browse pre-built templates
                </button>
                <button
                  type="button"
                  onClick={openBuilder}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white hover:bg-primary-hover"
                >
                  <Plus size={14} strokeWidth={2.5} />
                  Create Use Case
                </button>
              </div>
            </div>
          ) : null}

          {yourUseCasesOrdered.length > 0 ? (
            <section className="space-y-4" aria-label="Active use cases">
              <div className="flex items-center gap-3">
                <h2 className="text-lg font-semibold leading-7 tracking-[-0.2px] text-text-primary">Your active use cases</h2>
                <span className="rounded-full bg-[#f4f4f6] px-2.5 py-0.5 text-[11px] font-semibold text-text-muted">
                  {yourUseCasesOrdered.length}
                </span>
              </div>
              <div
                className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
                data-tour-uc="cards"
              >
                {yourUseCasesOrdered.map((item) => (
                  <GalleryUseCaseCard
                    key={item.id}
                    galleryCategory="yours"
                    variant={cardVariant(item.actionType)}
                    title={item.title}
                    desc={item.problemSignal || item.items?.[0] || ""}
                    actionType={item.actionType}
                    isDraft={item.status === "draft"}
                    process={formatProcessFieldForUi(item.process, processes)}
                    kpiNames={getKpiNamesForCatalogId(item.id)}
                    onOpen={() => openGalleryUseCase(item)}
                    onMenuClick={() => handleMenuClick(item)}
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}
