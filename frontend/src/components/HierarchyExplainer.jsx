import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Breadcrumb from "./shared/Breadcrumb";
import { useBreadcrumbTrail } from "../hooks/useBreadcrumbTrail";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Bell,
  Blocks,
  ClipboardList,
  FolderTree,
  Gauge,
  GitBranch,
  Play,
  Sparkles,
} from "lucide-react";
import { useProcesses } from "../hooks/useProcesses";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { useTour } from "../tours/TourContext";

/**
 * How Enterprise GPT is organized.
 *
 * Reader-mode teaching page:
 *   Hero → 6 concept cards → connections diagram → live example carousel
 *   → where to go next.
 *
 * Also the anchor for the guided tour (Act I). Voiceover in the tour reads a
 * conversational script that references the cards on this page, so any copy
 * change here should stay consistent with the tour script under
 * frontend/src/tours/.
 */

function flattenActiveKpis(registry) {
  const out = [];
  for (const cat of registry?.categories || []) {
    for (const sec of cat?.sections || []) {
      for (const k of sec?.kpis || []) {
        if (k?.active !== false) out.push(k);
      }
    }
  }
  return out;
}

function collectExampleTriads(areas, processes, useCases, kpiRegistry, limit = 3) {
  const flatKpis = flattenActiveKpis(kpiRegistry);
  const out = [];
  for (const area of areas) {
    if (out.length >= limit) break;
    const proc = (processes || []).find((p) => p.businessAreaId === area.id);
    if (!proc) continue;
    const kpi = flatKpis.find((k) => k.businessAreaId === area.id);
    if (!kpi) continue;
    const useCase =
      (useCases || []).find((uc) => {
        const uArea = String(uc.businessArea || "").toLowerCase();
        return uArea === area.label.toLowerCase() || uArea === area.id.toLowerCase();
      }) || null;
    out.push({ area, process: proc, kpi, useCase });
  }
  return out;
}

const CONCEPTS = [
  {
    id: "business-area",
    icon: Blocks,
    name: "Business Area",
    tagline: "Department-level scope",
    definition:
      "A department-sized division of the business. Contains its own processes and KPIs. Use cases can span multiple business areas.",
    exampleTitle: "Inventory",
    exampleBody: "Covers stock levels, replenishment, and shrinkage across all stores.",
    tone: "primary",
    href: "/business-areas",
  },
  {
    id: "process",
    icon: GitBranch,
    name: "Process",
    tagline: "Recurring workflow",
    definition:
      "A recurring workflow inside a business area. Runs on a schedule or when triggered by a signal.",
    exampleTitle: "Smart Restock",
    exampleBody:
      "Predicts weekly demand for each SKU. Places replenishment orders every Sunday night.",
    tone: "primary",
    href: "/processes",
  },
  {
    id: "kpi",
    icon: Gauge,
    name: "KPI",
    tagline: "Performance metric",
    definition:
      "A quantitative metric with a defined target. Can attach to a business area, process, subject area, or use case. Not owned by any single layer.",
    exampleTitle: "Stockout Rate",
    exampleBody:
      "Percent of SKUs out of stock across active stores. Target: under 2 percent. Current: 3.4 percent.",
    tone: "kpi",
    pill: "Maps to any layer",
    href: "/kpis/registry",
  },
  {
    id: "use-case",
    icon: ClipboardList,
    name: "Use Case",
    tagline: "Cross-cutting AI workflow",
    definition:
      "A packaged AI workflow that answers business questions and takes action. Spans multiple business areas and processes. Always tied to a KPI so its impact is measurable.",
    exampleTitle: "Weekly SKU Rebalance",
    exampleBody:
      "Spans Inventory and Store Ops. Recommends store-to-store SKU transfers to prevent stockouts. Delivers the plan every Monday at 7 AM.",
    tone: "useCase",
    pill: "Cross-cutting",
    href: "/use-cases",
  },
  {
    id: "alert",
    icon: Bell,
    name: "Alert",
    tagline: "Threshold notification",
    definition:
      "Fires when a KPI crosses a defined threshold. Typically links to the use case that can address the issue.",
    exampleTitle: "12 SKUs at stockout risk in Store 42",
    exampleBody: "Fired when the stockout probability on tracked SKUs crossed 60 percent.",
    tone: "alert",
    href: "/alerts",
  },
  {
    id: "subject-area",
    icon: FolderTree,
    name: "Subject Area",
    tagline: "KPI grouping",
    definition:
      "A tag that groups related KPIs by theme. Used for browsing metrics by topic. Not a peer of the other concepts.",
    exampleTitle: "Stock Health",
    exampleBody: "Groups Stockout Rate, Days-of-Cover, and Aged Inventory under one label.",
    tone: "subjectArea",
    pill: "Filing lens",
    href: "/kpis/registry",
  },
];

const TONE_STYLES = {
  primary: {
    iconWrap: "bg-primary-light text-primary dark:bg-primary/20",
    border: "border-primary/25 hover:border-primary/60",
    accent: "text-primary",
  },
  kpi: {
    iconWrap: "bg-[#fff6ed] text-[#b93815] dark:bg-[#b93815]/15 dark:text-[#fdb075]",
    border: "border-[#f1d4bd] hover:border-[#b93815]/60",
    accent: "text-[#b93815] dark:text-[#fdb075]",
  },
  useCase: {
    iconWrap: "bg-[#ecfdf3] text-[#067647] dark:bg-[#067647]/15 dark:text-[#6ce9a6]",
    border: "border-[#a9f0d1] hover:border-[#067647]/60",
    accent: "text-[#067647] dark:text-[#6ce9a6]",
  },
  alert: {
    iconWrap: "bg-[#fef3f2] text-[#b42318] dark:bg-[#b42318]/15 dark:text-[#f97066]",
    border: "border-[#fecdca] hover:border-[#b42318]/60",
    accent: "text-[#b42318] dark:text-[#f97066]",
  },
  subjectArea: {
    iconWrap: "bg-surface-secondary text-text-secondary dark:bg-surface-tertiary",
    border: "border-border hover:border-primary/40",
    accent: "text-text-secondary",
  },
};

function ConceptCard({ concept }) {
  const styles = TONE_STYLES[concept.tone] || TONE_STYLES.primary;
  const Icon = concept.icon;
  const inner = (
    <div
      data-tour-concept={concept.id}
      className={`flex h-full flex-col rounded-2xl border-2 ${styles.border} bg-surface p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md`}
    >
      <div className="flex items-start justify-between gap-3">
        <div
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl ${styles.iconWrap}`}
          aria-hidden
        >
          <Icon className="h-6 w-6" strokeWidth={1.75} />
        </div>
        {concept.pill ? (
          <span className="rounded-full border border-dashed border-border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
            {concept.pill}
          </span>
        ) : null}
      </div>
      <div className="mt-4">
        <p className={`text-[11px] font-semibold uppercase tracking-wide ${styles.accent}`}>
          {concept.tagline}
        </p>
        <h3 className="mt-1 text-xl font-semibold text-text-primary">{concept.name}</h3>
        <p className="mt-2 text-sm leading-relaxed text-text-secondary">{concept.definition}</p>
      </div>
      <div className="mt-4 border-t border-border pt-3">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Example</p>
        <p className="mt-0.5 text-sm font-semibold text-text-primary">{concept.exampleTitle}</p>
        <p className="mt-1 text-xs leading-relaxed text-text-secondary">{concept.exampleBody}</p>
      </div>
    </div>
  );
  return concept.href ? (
    <Link to={concept.href} className="block h-full">
      {inner}
    </Link>
  ) : (
    inner
  );
}

function ConnectionEdge({ label, arrow = "→" }) {
  return (
    <div className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] font-semibold text-text-secondary">
      {arrow === "←" ? <ArrowLeft className="h-3 w-3" strokeWidth={2.5} aria-hidden /> : null}
      <span>{label}</span>
      {arrow === "→" ? <ArrowRight className="h-3 w-3" strokeWidth={2.5} aria-hidden /> : null}
    </div>
  );
}

function ConnectionsDiagram() {
  return (
    <div className="rounded-2xl border border-border bg-surface p-6 sm:p-8">
      <div className="mx-auto grid max-w-3xl gap-3 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
        <div className="rounded-xl border-2 border-primary/25 bg-primary-light/40 px-4 py-3 text-center dark:bg-primary/15">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-primary">Business Area</p>
          <p className="mt-0.5 text-sm font-semibold text-text-primary">contains</p>
        </div>
        <div className="flex justify-center sm:justify-center">
          <ConnectionEdge label="contains" />
        </div>
        <div className="rounded-xl border-2 border-primary/25 bg-primary-light/40 px-4 py-3 text-center dark:bg-primary/15">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-primary">Process</p>
          <p className="mt-0.5 text-sm font-semibold text-text-primary">is measured by</p>
        </div>
      </div>

      <div className="mx-auto mt-4 flex max-w-3xl justify-center">
        <div className="h-6 w-px bg-border" />
      </div>

      <div className="mx-auto grid max-w-3xl gap-3 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
        <div className="rounded-xl border-2 border-[#a9f0d1] bg-[#ecfdf3]/60 px-4 py-3 text-center dark:bg-[#067647]/15">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[#067647] dark:text-[#6ce9a6]">
            Use Case
          </p>
          <p className="mt-0.5 text-sm font-semibold text-text-primary">moves</p>
        </div>
        <div className="flex justify-center">
          <ConnectionEdge label="moves" />
        </div>
        <div className="rounded-xl border-2 border-[#f1d4bd] bg-[#fff6ed]/60 px-4 py-3 text-center dark:bg-[#b93815]/15">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[#b93815] dark:text-[#fdb075]">
            KPI
          </p>
          <p className="mt-0.5 text-sm font-semibold text-text-primary">triggers</p>
        </div>
      </div>

      <div className="mx-auto mt-4 flex max-w-3xl justify-center">
        <div className="h-6 w-px bg-border" />
      </div>

      <div className="mx-auto flex max-w-3xl justify-center">
        <div className="rounded-xl border-2 border-[#fecdca] bg-[#fef3f2]/60 px-4 py-3 text-center dark:bg-[#b42318]/15">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[#b42318] dark:text-[#f97066]">
            Alert
          </p>
          <p className="mt-0.5 text-sm font-semibold text-text-primary">signals what needs attention</p>
        </div>
      </div>

      <p className="mx-auto mt-6 max-w-2xl text-center text-sm leading-relaxed text-text-secondary">
        A Business Area contains Processes. A Process is measured by KPIs. A Use Case runs across
        one or more Business Areas and moves a KPI. A KPI triggers an Alert when it crosses a
        threshold. Subject Areas group related KPIs for browsing.
      </p>
    </div>
  );
}

function LiveExample({ triad }) {
  if (!triad) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-surface-secondary/60 p-6 text-center">
        <p className="text-sm text-text-secondary">
          Add a process, use case, and KPI for any business area to see a live example here.
        </p>
      </div>
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-[1fr_auto_1fr_auto_1fr]">
      {/* Area */}
      <div className="rounded-xl border border-primary/25 bg-primary-light/40 p-4 dark:bg-primary/15">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-primary">Business Area</p>
        <p className="mt-1 text-sm font-semibold text-text-primary">{triad.area.label}</p>
      </div>
      <div className="flex items-center justify-center">
        <ArrowRight className="h-5 w-5 text-text-muted" strokeWidth={2} aria-hidden />
      </div>
      {/* Process */}
      <div className="rounded-xl border border-primary/25 bg-primary-light/40 p-4 dark:bg-primary/15">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-primary">Process</p>
        <p className="mt-1 text-sm font-semibold text-text-primary">{triad.process.name}</p>
      </div>
      <div className="flex items-center justify-center">
        <ArrowRight className="h-5 w-5 text-text-muted" strokeWidth={2} aria-hidden />
      </div>
      {/* KPI */}
      <div className="rounded-xl border border-[#f1d4bd] bg-[#fff6ed]/60 p-4 dark:bg-[#b93815]/15">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-[#b93815] dark:text-[#fdb075]">
          KPI
        </p>
        <p className="mt-1 text-sm font-semibold text-text-primary">{triad.kpi.name}</p>
      </div>
      {/* Use case row spans full width below */}
      {triad.useCase ? (
        <div className="col-span-full mt-2 flex items-center justify-center gap-2 text-xs text-text-secondary">
          <span className="rounded-full border border-[#a9f0d1] bg-[#ecfdf3]/60 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-[#067647] dark:bg-[#067647]/15 dark:text-[#6ce9a6]">
            Use Case moving this KPI
          </span>
          <span className="font-medium text-text-primary">{triad.useCase.title}</span>
        </div>
      ) : null}
    </div>
  );
}

function LiveExampleCarousel({ triads }) {
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    if (idx >= triads.length) setIdx(0);
  }, [idx, triads.length]);
  if (triads.length === 0) {
    return <LiveExample triad={null} />;
  }
  const current = triads[idx];
  return (
    <div>
      <LiveExample triad={current} />
      {triads.length > 1 ? (
        <div className="mt-4 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => setIdx((i) => (i - 1 + triads.length) % triads.length)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-border bg-surface text-text-secondary transition hover:border-primary hover:text-primary"
            aria-label="Previous example"
          >
            <ArrowLeft size={16} strokeWidth={2} />
          </button>
          <span className="text-xs font-medium text-text-muted">
            Example {idx + 1} of {triads.length}
          </span>
          <button
            type="button"
            onClick={() => setIdx((i) => (i + 1) % triads.length)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-border bg-surface text-text-secondary transition hover:border-primary hover:text-primary"
            aria-label="Next example"
          >
            <ArrowRight size={16} strokeWidth={2} />
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default function HierarchyExplainer() {
  const trail = useBreadcrumbTrail("How it's organized", [{ label: "Playground", to: "/playground" }]);
  const { processes } = useProcesses();
  const { entries: useCases } = useUseCaseCatalog();
  const { data: kpiRegistry } = useKpiRegistry();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();

  const triads = useMemo(
    () => collectExampleTriads(BUSINESS_AREAS, processes || [], useCases || [], kpiRegistry || {}, 3),
    [BUSINESS_AREAS, processes, useCases, kpiRegistry]
  );

  const { startTour } = useTour();

  return (
    <div className="mx-auto min-h-[calc(100vh-72px)] max-w-6xl px-6 py-10">
      <Breadcrumb items={trail} />

      {/* Hero */}
      <header className="mt-4 rounded-3xl border border-primary/25 bg-gradient-to-br from-primary-light/40 via-surface to-surface p-8 shadow-sm sm:p-10 dark:from-primary/15 dark:via-surface-default dark:to-surface-default">
        <div className="inline-flex items-center gap-2 rounded-full bg-primary-light px-3 py-1 text-xs font-semibold uppercase tracking-wide text-primary dark:bg-primary/25">
          <Sparkles className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden />
          How Enterprise GPT works
        </div>
        <h1 className="mt-4 max-w-3xl text-4xl font-semibold tracking-tight text-text-primary sm:text-5xl">
          The building blocks of Enterprise GPT.
        </h1>
        <p className="mt-3 max-w-2xl text-base leading-relaxed text-text-secondary">
          Business areas contain processes. Processes are measured by KPIs. Use cases run across
          multiple business areas and move those KPIs. Alerts fire when a KPI crosses a threshold.
          These six concepts define how the entire product is organized.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => startTour("whole-product")}
            data-tour-launch="whole-product"
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover"
          >
            <Play className="h-4 w-4" strokeWidth={2} aria-hidden />
            Start guided tour · 5 min
          </button>
          <span className="text-xs text-text-muted">Includes voice narration. Pause any time.</span>
        </div>
      </header>

      {/* Six concept cards */}
      <section className="mt-14" data-tour-hierarchy="building-blocks">
        <div className="mb-6 flex items-baseline justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">01 · Concepts</p>
            <h2 className="mt-1 text-2xl font-semibold text-text-primary">The six building blocks</h2>
          </div>
          <p className="hidden max-w-md text-sm text-text-secondary sm:block">
            Every entity in Enterprise GPT is one of these six concepts.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {CONCEPTS.map((c) => (
            <ConceptCard key={c.id} concept={c} />
          ))}
        </div>
      </section>

      {/* Connections */}
      <section className="mt-14" data-tour-hierarchy="connections">
        <div className="mb-6">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">02 · Relationships</p>
          <h2 className="mt-1 text-2xl font-semibold text-text-primary">How the concepts connect</h2>
        </div>
        <ConnectionsDiagram />
      </section>

      {/* Live example */}
      <section className="mt-14">
        <div className="mb-6 flex items-baseline justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">03 · Live examples</p>
            <h2 className="mt-1 text-2xl font-semibold text-text-primary">Real triads from this workspace</h2>
          </div>
          <p className="hidden max-w-md text-sm text-text-secondary sm:block">
            Pulled from the current profile's KPIs, processes, and use cases.
          </p>
        </div>
        <div className="rounded-2xl border border-border bg-surface p-6 sm:p-8">
          <LiveExampleCarousel triads={triads} />
        </div>
      </section>

      {/* Where to go next */}
      <section className="mt-14 mb-6">
        <div className="mb-6">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">04 · Related sections</p>
          <h2 className="mt-1 text-2xl font-semibold text-text-primary">Continue in the product</h2>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Link
            to="/playground"
            className="flex items-center gap-3 rounded-xl border border-border bg-surface p-4 text-sm font-semibold text-text-primary transition hover:border-primary hover:text-primary hover:shadow-sm"
          >
            <Sparkles className="h-5 w-5 text-primary" strokeWidth={1.75} aria-hidden />
            Try the playground
          </Link>
          <Link
            to="/kpis/registry"
            className="flex items-center gap-3 rounded-xl border border-border bg-surface p-4 text-sm font-semibold text-text-primary transition hover:border-primary hover:text-primary hover:shadow-sm"
          >
            <Gauge className="h-5 w-5 text-primary" strokeWidth={1.75} aria-hidden />
            Open the KPI Registry
          </Link>
          <Link
            to="/use-cases"
            className="flex items-center gap-3 rounded-xl border border-border bg-surface p-4 text-sm font-semibold text-text-primary transition hover:border-primary hover:text-primary hover:shadow-sm"
          >
            <ClipboardList className="h-5 w-5 text-primary" strokeWidth={1.75} aria-hidden />
            Browse use cases
          </Link>
          <Link
            to="/processes"
            className="flex items-center gap-3 rounded-xl border border-border bg-surface p-4 text-sm font-semibold text-text-primary transition hover:border-primary hover:text-primary hover:shadow-sm"
          >
            <Activity className="h-5 w-5 text-primary" strokeWidth={1.75} aria-hidden />
            Review processes
          </Link>
        </div>
      </section>
    </div>
  );
}
