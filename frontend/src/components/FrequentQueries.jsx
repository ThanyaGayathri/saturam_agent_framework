import { useMemo, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpLeft,
  BarChart3,
  ChartLine,
  Headphones,
  MessageSquare,
  Monitor,
  Package,
  Search,
  ShoppingCart,
  Store,
  Tag,
  TrendingUp,
  Users,
  X,
} from "lucide-react";
import { ExplorePromptSeverityIcon } from "./shared/ExplorePromptSeverityIcon";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getExplorePromptsForCatalogEntry, formatExplorePromptForComposer } from "../domain/demo-use-case-prompts";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { FEATURED_IMPACT } from "../domain/featured-use-cases";
import TourLaunchButton from "../tours/TourLaunchButton";

const BUSINESS_AREA_ICONS = {
  inventory: Package,
  orders: ShoppingCart,
  merchandise: Tag,
  "store-ops": Store,
  "e-com": ChartLine,
  pos: Monitor,
  support: Headphones,
  pricing: TrendingUp,
  finance: BarChart3,
  workforce: Users,
};

export default function FrequentQueries() {
  const navigate = useNavigate();
  const { entries, hydrated } = useUseCaseCatalog();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();
  const [search, setSearch] = useState("");

  const groupedByArea = useMemo(() => {
    const seedEntries = (entries || []).filter((e) => e.source === "seed");
    const map = new Map();
    for (const ba of BUSINESS_AREAS) {
      map.set(ba.label, []);
    }
    for (const e of seedEntries) {
      const label = e.businessArea;
      if (!label || !map.has(label)) continue;
      const promptsRaw = getExplorePromptsForCatalogEntry(e);
      const prompts = promptsRaw.map((p, explorePromptIndex) => ({
        id: `${e.id}-${p.lens || p.horizon || "p"}`,
        areaLabel: label,
        catalogId: e.id,
        useCaseTitle: e.title,
        lens: p.lens,
        horizon: p.horizon,
        signal: p.signal,
        explorePromptIndex,
        text: p.text,
      }));
      map.get(label).push({
        catalogId: e.id,
        title: e.title,
        problemSignal: e.problemSignal || "",
        prompts,
      });
    }
    return map;
  }, [entries, BUSINESS_AREAS]);

  const filteredGroupedByArea = useMemo(() => {
    const q = search.trim().toLowerCase();
    const out = new Map();
    for (const [label, groups] of groupedByArea) {
      if (!q) {
        out.set(label, groups);
        continue;
      }
      const areaMatches = label.toLowerCase().includes(q);
      const nextGroups = [];
      for (const g of groups) {
        const impactLine = (FEATURED_IMPACT[g.catalogId] || g.problemSignal || "").toLowerCase();
        const metaMatches =
          g.title.toLowerCase().includes(q) ||
          impactLine.includes(q);
        const promptsHit = g.prompts.filter((p) => p.text.toLowerCase().includes(q));
        let displayPrompts;
        if (areaMatches || metaMatches) {
          displayPrompts = promptsHit.length > 0 ? promptsHit : g.prompts;
        } else {
          displayPrompts = promptsHit;
        }
        if (displayPrompts.length > 0) {
          nextGroups.push({ ...g, prompts: displayPrompts });
        }
      }
      out.set(label, nextGroups);
    }
    return out;
  }, [groupedByArea, search]);

  const totalQueries = useMemo(() => {
    let n = 0;
    for (const groups of groupedByArea.values()) {
      for (const g of groups) n += g.prompts.length;
    }
    return n;
  }, [groupedByArea]);

  const totalShown = useMemo(() => {
    let n = 0;
    for (const groups of filteredGroupedByArea.values()) {
      for (const g of groups) n += g.prompts.length;
    }
    return n;
  }, [filteredGroupedByArea]);

  const areasToRender = BUSINESS_AREAS.filter(
    (ba) => (filteredGroupedByArea.get(ba.label) || []).length > 0
  );

  const openPromptOnPlaygroundHub = useCallback(
    (it) => {
      const prefill = formatExplorePromptForComposer({
        lens: it.lens,
        text: it.text,
      });
      if (!prefill) return;
      navigate("/playground", { state: { prefillQuery: prefill } });
    },
    [navigate]
  );

  if (!hydrated) {
    return (
      <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-5 py-10 sm:px-8">
        <p className="text-sm text-text-secondary">Loading queries…</p>
      </div>
    );
  }

  return (
    <div className="min-h-full w-full bg-transparent">
      <div className="mx-auto w-full max-w-[1440px] px-5 py-6 sm:px-8">
        <Link
          to="/playground"
          className="inline-flex items-center gap-1.5 text-sm font-semibold text-text-tertiary transition hover:text-primary"
        >
          <ArrowLeft size={14} /> Back to Playground
        </Link>

        <header className="mt-4 flex flex-col gap-3 border-b border-border pb-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0 flex-1">
            <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-text-primary">
              <MessageSquare size={22} className="text-primary" strokeWidth={2} />
              Frequent queries
              <TourLaunchButton tourId="frequent-queries" label="Take a quick Frequent queries tour" />
            </h1>
            <p className="mt-1.5 max-w-2xl text-sm leading-6 text-text-secondary sm:text-[15px]">
              Sample questions grouped by business area and use case. Click a query to open Playground with it in the search box, then press Enter when you want to chat.
            </p>
            <p className="mt-2 text-sm leading-5 text-text-muted" aria-live="polite">
              {search.trim()
                ? `${totalShown} of ${totalQueries} ${totalQueries === 1 ? "query" : "queries"} match your search`
                : `${totalQueries} ${totalQueries === 1 ? "query" : "queries"} across ${BUSINESS_AREAS.length} business areas`}
            </p>
          </div>
        </header>

        <div className="mt-4 flex w-full items-center gap-2 rounded-xl border border-border bg-surface p-1.5 shadow-sm sm:p-2" data-tour-freq="search">
          <div className="relative w-full">
            <Search
              size={16}
              strokeWidth={2}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
              aria-hidden
            />
            <input
              id="frequent-queries-search"
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search queries by keyword, use case, or area"
              autoComplete="off"
              className="h-10 w-full rounded-lg border border-transparent bg-transparent py-0 pl-10 pr-9 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            {search ? (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-text-muted transition hover:bg-surface-secondary hover:text-text-secondary"
                aria-label="Clear search"
              >
                <X size={15} strokeWidth={2} />
              </button>
            ) : null}
          </div>
        </div>

        {areasToRender.length === 0 ? (
          <div className="mt-6 rounded-2xl border border-dashed border-border bg-surface p-8 text-center shadow-sm">
            <p className="text-sm font-medium text-text-primary">No queries match your search</p>
            <p className="mt-1 text-sm text-text-secondary">Try a different keyword or clear the search.</p>
          </div>
        ) : (
          <div className="mt-6 space-y-6" data-tour-freq="areas">
            {areasToRender.map((ba) => {
              const groups = filteredGroupedByArea.get(ba.label) || [];
              const AreaIcon = BUSINESS_AREA_ICONS[ba.id] || Package;
              const promptCount = groups.reduce((acc, g) => acc + g.prompts.length, 0);
              return (
                <section
                  key={ba.id}
                  id={`area-${ba.id}`}
                  className="scroll-mt-24 overflow-hidden rounded-2xl border border-border bg-surface shadow-sm"
                >
                  <div className="flex items-center justify-between gap-3 border-b border-border bg-surface-page-top/60 px-4 py-3 sm:px-6">
                    <div className="flex items-center gap-3">
                      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-primary/25 bg-primary-light text-primary shadow-sm ring-1 ring-primary/10 dark:bg-primary/20 dark:ring-primary/20 dark:border-primary/40">
                        <AreaIcon size={16} strokeWidth={1.85} aria-hidden />
                      </span>
                      <div>
                        <h2 className="text-base font-semibold leading-tight text-text-primary sm:text-[17px]">{ba.label}</h2>
                        <p className="mt-0.5 text-xs text-text-muted">
                          {promptCount} {promptCount === 1 ? "query" : "queries"} · {groups.length}{" "}
                          {groups.length === 1 ? "use case" : "use cases"}
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => navigate(`/playground/area/${ba.id}`)}
                      className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs font-semibold text-text-tertiary transition hover:border-primary/30 hover:text-primary"
                    >
                      Open area <ArrowRight size={12} />
                    </button>
                  </div>
                  <div className="grid grid-cols-1 gap-5 p-4 sm:p-5 md:grid-cols-2 xl:grid-cols-3">
                    {groups.map((g) => (
                      <div
                        key={g.catalogId}
                        className="flex min-h-0 flex-col rounded-2xl border border-border bg-surface p-5 shadow-sm transition-all duration-300 ease-out hover:-translate-x-1 hover:-translate-y-1 hover:border-primary/40 hover:bg-primary-light/50 hover:shadow-[0_8px_24px_rgba(105,65,198,0.12)] dark:hover:bg-primary/10 dark:shadow-none dark:hover:shadow-[0_8px_24px_rgba(105,65,198,0.2)] sm:p-6"
                      >
                        <div className="min-w-0">
                          <h3 className="text-lg font-semibold leading-snug text-text-primary">{g.title}</h3>
                          {FEATURED_IMPACT[g.catalogId] || g.problemSignal ? (
                            <p className="mt-1 text-sm leading-snug text-text-secondary">
                              {FEATURED_IMPACT[g.catalogId] || g.problemSignal}
                            </p>
                          ) : null}
                        </div>
                        <ul className="mt-4 space-y-2">
                          {g.prompts.map((it) => (
                            <li key={it.id} className="list-none">
                              <div className="group/query relative rounded-lg transition-all duration-200 ease-out hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[0_6px_18px_rgba(105,65,198,0.1)]">
                                <button
                                  type="button"
                                  onClick={() => openPromptOnPlaygroundHub(it)}
                                  className="flex w-full items-start rounded-lg border border-border bg-surface-secondary px-3 py-2.5 pb-9 pr-10 text-left text-sm text-text-secondary transition-colors duration-200 ease-out group-hover/query:border-primary/40 group-hover/query:bg-primary-light/40 group-hover/query:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 dark:group-hover/query:bg-primary/15"
                                >
                                  <span className="min-w-0 flex-1">
                                    <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                                      {it.lens ? <span>{it.lens}</span> : null}
                                      <ExplorePromptSeverityIcon
                                        prompt={{
                                          lens: it.lens,
                                          horizon: it.horizon,
                                          signal: it.signal,
                                        }}
                                        indexInCard={it.explorePromptIndex}
                                      />
                                    </span>
                                    <span className="mt-0.5 block leading-snug">{it.text}</span>
                                  </span>
                                </button>
                                <button
                                  type="button"
                                  className="absolute bottom-2 right-3 rounded p-0.5 text-text-muted transition-all duration-200 ease-out hover:bg-surface hover:text-primary group-hover/query:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 dark:hover:bg-surface-tertiary"
                                  aria-label="Add this question to Playground"
                                  onClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    openPromptOnPlaygroundHub(it);
                                  }}
                                >
                                  <ArrowUpLeft
                                    size={16}
                                    strokeWidth={2}
                                    className="transition-transform duration-200 ease-out group-hover/query:-translate-x-0.5 group-hover/query:-translate-y-0.5"
                                    aria-hidden
                                  />
                                </button>
                              </div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
