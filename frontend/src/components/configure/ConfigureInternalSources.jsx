import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Database, Link2, MoreVertical, Pencil, Plus, Sparkles, Trash2, X } from "lucide-react";
import { CatalogSearchToolbar } from "../shared/CatalogSearchToolbar";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { internalSourceTypeLabel, externalIntegrationsForProfile } from "../../domain/configure-catalogs";
import { MODEL_STATES, formatSync, metricsUnlockedBy, modelStateFor, workspaceReadiness } from "./source-model-state";
import postgresSvg from "../../assets/postgres.svg";
import snowflakeSvg from "../../assets/snowflake.svg";
import sapSvg from "../../assets/sap.svg";
import salesforceSvg from "../../assets/salesforce.svg";
import airflowSvg from "../../assets/airflow.svg";
import awsGlueSvg from "../../assets/aws-glue.svg";
import azureAdfSvg from "../../assets/azure-data-factory.svg";
import netSuiteSvg from "../../assets/netsuite.svg";
import shopifySvg from "../../assets/shopify.svg";
import slackSvg from "../../assets/slack.svg";

/**
 * Everything the workspace is connected to, in one place.
 *
 * Databases and SaaS platforms used to live on separate tabs, split by how we reach them --
 * a connection string versus an API. That is our implementation detail, not a distinction the
 * user has: Salesforce appeared on both tabs, so "where do I add Salesforce?" had no answer.
 * They are one list now, filtered by kind.
 *
 * The card reports SEMANTIC-LAYER READINESS rather than reachability. Whether a connection is
 * alive says nothing about whether the product can answer a question from it, and readiness is
 * the thing that gates suggestions, dashboards and the playground.
 */

const SOURCE_ICON = {
 postgres: postgresSvg,
 snowflake: snowflakeSvg,
 sap_s4hana: sapSvg,
 salesforce: salesforceSvg,
 hubspot: salesforceSvg,
 logrocket: postgresSvg,
 bigquery: snowflakeSvg,
 s3: awsGlueSvg,
 airflow: airflowSvg,
 aws_glue: awsGlueSvg,
 mssql: azureAdfSvg,
 dynamics365: azureAdfSvg,
 netsuite: netSuiteSvg,
 shopify: shopifySvg,
 slack: slackSvg,
 sfcc: salesforceSvg,
};

const BRAND_COLOR = {
 klaviyo: { bg: "#00b8d9", initial: "K" },
 stripe: { bg: "#635bff", initial: "S" },
 magento: { bg: "#ee6723", initial: "M" },
 braze: { bg: "#09c", initial: "B" },
 teams: { bg: "#5059c9", initial: "T" },
 fedex: { bg: "#4d148c", initial: "F" },
 webhook: { bg: "#535862", initial: "W" },
};

/** Warehouses answer analytical questions; the split matters when choosing what to model first. */
const WAREHOUSE_TYPES = new Set(["snowflake", "bigquery", "redshift"]);

function kindOf(source) {
 if (source.kind === "platform") return "platform";
 return WAREHOUSE_TYPES.has(source.sourceType) ? "warehouse" : "database";
}

const FILTERS = [
 { id: "all", label: "All" },
 { id: "database", label: "Databases" },
 { id: "warehouse", label: "Warehouses" },
 { id: "platform", label: "Platforms" },
];

function SourceIcon({ typeId }) {
 const src = SOURCE_ICON[typeId];
 if (src) {
  return (
   <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-surface p-2">
    <img src={src} alt="" className="h-full w-full object-contain" />
   </div>
  );
 }
 const branded = BRAND_COLOR[typeId];
 if (branded) {
  return (
   <div
    className="flex h-12 w-12 items-center justify-center rounded-xl text-base font-bold text-white"
    style={{ backgroundColor: branded.bg }}
   >
    {branded.initial}
   </div>
  );
 }
 return (
  <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-light dark:bg-primary/20">
   <Database size={22} className="text-primary" />
  </div>
 );
}

function SourceCard({ source, model, onEdit, onRemove, onOpenSemanticLayer, onSeeUnlocked }) {
 const [menuOpen, setMenuOpen] = useState(false);
 const pill = MODEL_STATES[model.state] || MODEL_STATES.not_started;
 const unlocked = metricsUnlockedBy(source.sourceType);

 return (
  <div className="relative flex flex-col rounded-2xl border border-border bg-surface p-5 transition-shadow hover:shadow-md">
   <div className="flex items-start justify-between gap-3">
    <SourceIcon typeId={source.iconId} />
    <div className="flex shrink-0 items-center gap-1.5">
     <span className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${pill.pill}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${pill.dot}`} />
      {pill.label}
     </span>
     <div className="relative">
      <button
       type="button"
       onClick={() => setMenuOpen((v) => !v)}
       className="rounded p-1 text-text-muted hover:bg-surface-tertiary"
       aria-label="More options"
      >
       <MoreVertical size={16} />
      </button>
      {menuOpen && (
       <div className="absolute right-0 top-full z-20 mt-1 w-36 overflow-hidden rounded-xl border border-border bg-surface shadow-xl">
        <button
         type="button"
         onClick={() => { setMenuOpen(false); onEdit(); }}
         className="flex w-full items-center gap-2 px-3 py-2.5 text-sm text-text-primary hover:bg-surface-secondary"
        >
         <Pencil size={14} />
         Edit
        </button>
        <button
         type="button"
         onClick={() => { setMenuOpen(false); onRemove(); }}
         className="flex w-full items-center gap-2 px-3 py-2.5 text-sm text-status-critical hover:bg-status-critical-light"
        >
         <Trash2 size={14} />
         Remove
        </button>
       </div>
      )}
     </div>
    </div>
   </div>

   <h3 className="mt-3 font-bold text-text-primary">{source.name}</h3>
   <p className="mb-3 mt-0.5 truncate text-xs text-text-muted">{source.subtitle}</p>

   {/* Mid-build: counts are still moving, so progress replaces them rather than showing a
       number that will be wrong in ten seconds. */}
   {model.state === "building" ? (
    <div className="mt-auto border-t border-border pt-3">
     <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-tertiary">
      <div
       className="h-full rounded-full bg-primary transition-[width] duration-500"
       style={{ width: `${model.progress || 0}%` }}
      />
     </div>
     <p className="mt-2 text-xs text-text-muted">{model.step || "Building semantic layer…"}</p>
    </div>
   ) : (
    <div className="mt-auto flex justify-between border-t border-border pt-3">
     <div>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
       {model.tables != null ? "Modelled" : "Type"}
      </p>
      <p className="mt-0.5 truncate text-sm font-semibold text-text-primary">
       {model.tables != null ? `${model.modelled ?? 0} of ${model.tables} tables` : source.typeLabel}
      </p>
     </div>
     <div className="text-right">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Last sync</p>
      {/* "Never" rather than a fabricated time. The old card printed "2 mins ago" as a literal
          on every source, including ones that had never synced. */}
      <p className="mt-0.5 text-sm font-semibold text-text-primary">{formatSync(model.syncedMinsAgo)}</p>
     </div>
    </div>
   )}

   {/* The semantic layer is reachable from the source it belongs to. The entry point appears as
       soon as a source is connected -- only what it opens changes with state -- because the
       "needs review" case is precisely the one that needs the user and would otherwise have no
       way in. */}
   {model.state === "needs_review" && (
    <button
     type="button"
     onClick={onOpenSemanticLayer}
     className="mt-3 w-full rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
    >
     Review {model.reviewItems} item{model.reviewItems === 1 ? "" : "s"}
    </button>
   )}
   {/* What the source actually bought you. "63 of 63 tables" is plumbing; this is the answer to
       the question someone is really asking after connecting something. */}
   {model.state === "modelled" && unlocked.length > 0 && (
    <button
     type="button"
     onClick={onSeeUnlocked}
     className="mt-3 flex w-full items-center justify-between gap-2 rounded-lg border border-primary/30 bg-primary-light/50 px-3 py-2 text-left text-sm font-semibold text-primary transition-colors hover:bg-primary-light dark:bg-primary/10"
    >
     <span className="inline-flex items-center gap-1.5">
      <Sparkles size={13} />
      {unlocked.length} metrics unlocked
     </span>
     <ArrowRight size={14} />
    </button>
   )}

   {model.state === "modelled" && (
    <button
     type="button"
     onClick={onOpenSemanticLayer}
     className="mt-2 w-full rounded-lg border border-border-light bg-surface px-3 py-2 text-sm font-semibold text-text-primary transition-colors hover:bg-surface-secondary"
    >
     View semantic layer
    </button>
   )}
   {model.state === "error" && (
    <p className="mt-3 text-xs text-status-critical">
     {model.error || "Modelling failed. Check the connection and retry."}
    </p>
   )}
  </div>
 );
}

export default function ConfigureInternalSources() {
 const navigate = useNavigate();
 const { profileId } = useDemoProfile();
 const { data, hydrated, removeInternal, removeExternal } = useConfigureWorkspace();
 const [search, setSearch] = useState("");
 const [filter, setFilter] = useState("all");


 const sources = useMemo(() => {
  // Seeded demo rows (`seed: true`) are static placeholders, not real connections -- hide
  // them from the grid (and everything computed from it: counts, readiness) so this page
  // only ever shows sources someone actually connected. They stay in storage rather than
  // being deleted, so nothing here reverts a user's own edits to an unrelated row.
  const internal = (data.internalSources || [])
   .filter((s) => !s.seed)
   .map((s) => ({
   id: s.id,
   name: s.name,
   subtitle: `${s.host}${s.port ? `:${s.port}` : ""}`,
   iconId: s.sourceType,
   typeLabel: internalSourceTypeLabel(s.sourceType),
   sourceType: s.sourceType,
   kind: null,
   editPath: `/configure/internal-sources/edit/${encodeURIComponent(s.id)}`,
   remove: () => removeInternal(s.id),
  }));

  const catalog = externalIntegrationsForProfile(profileId);
  const labelFor = (pid) => catalog.find((c) => c.id === pid)?.label || pid;
  const categoryFor = (pid) => catalog.find((c) => c.id === pid)?.category || "Platform";

  // `normalizeExternal` doesn't carry a `seed` flag through (unlike `normalizeInternal`), so
  // fall back to the one thing every seed row reliably has: its id, always built as
  // `seed_*` (see `CONFIGURE_SEED_EXTERNAL`), never something a real integration gets --
  // `createExternalIntegration` always mints `ext_*` via `genId`.
  const external = (data.externalIntegrations || [])
   .filter((e) => !String(e.id || "").startsWith("seed_"))
   .map((e) => ({
   id: e.id,
   name: e.name || labelFor(e.providerId),
   subtitle: e.baseUrl || categoryFor(e.providerId),
   iconId: e.providerId,
   typeLabel: categoryFor(e.providerId),
   sourceType: e.providerId,
   kind: "platform",
   editPath: `/configure/external/edit/${encodeURIComponent(e.id)}`,
   remove: () => removeExternal(e.id),
  }));

  return [...internal, ...external];
 }, [data.internalSources, data.externalIntegrations, profileId, removeInternal, removeExternal]);

 const readiness = useMemo(() => workspaceReadiness(sources), [sources]);

 // Sources that finished modelling while the user was on this screen. The unlock banner is a
 // MOMENT -- it is worth showing when it happens, not as a permanent fixture -- so it is keyed on
 // the transition rather than on the state.
 const [justCompleted, setJustCompleted] = useState(null);
 // Presentation-only completion of the in-flight build, so the transition (and the banner it
 // triggers) can actually be seen. The real pipeline replaces this; the UI around it does not
 // change.
 const [completedIds, setCompletedIds] = useState(() => new Set());
 const [progress, setProgress] = useState({});

 const buildingSources = useMemo(
  () => sources.filter((s) => modelStateFor(s.id).state === "building" && !completedIds.has(s.id)),
  [sources, completedIds]
 );

 useEffect(() => {
  if (buildingSources.length === 0) return undefined;
  const target = buildingSources[0];
  const base = modelStateFor(target.id).progress || 0;
  const started = Date.now();
  const id = setInterval(() => {
   const pct = Math.min(100, base + ((Date.now() - started) / 7000) * (100 - base));
   setProgress((prev) => ({ ...prev, [target.id]: pct }));
   if (pct >= 100) {
    clearInterval(id);
    setCompletedIds((prev) => new Set(prev).add(target.id));
    setJustCompleted(target);
   }
  }, 220);
  return () => clearInterval(id);
 }, [buildingSources]);

 // Resolved state for a card: the seeded state, with the in-flight build's progress applied and
 // its completion honoured.
 const stateFor = (source) => {
  const model = modelStateFor(source.id);
  if (completedIds.has(source.id)) {
   return { ...model, state: "modelled", modelled: model.tables ?? model.modelled, syncedMinsAgo: 0 };
  }
  if (model.state === "building" && progress[source.id] != null) {
   return { ...model, progress: progress[source.id] };
  }
  return model;
 };

 // Databases and connected platforms, normalised into one shape so the card does not have to
 // know which of the two stores a row came from.

 const filtered = useMemo(() => {
  let list = sources;
  if (filter !== "all") list = list.filter((s) => kindOf(s) === filter);
  if (search.trim()) {
   const q = search.toLowerCase();
   list = list.filter(
    (s) => s.name.toLowerCase().includes(q) || String(s.typeLabel).toLowerCase().includes(q)
   );
  }
  return list;
 }, [sources, filter, search]);

 if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

 return (
  <div className="space-y-5">
   {/* The moment a source becomes useful. Dismissible and transient by design: it answers
       "was connecting that worth it?", which is a question asked once, not a status to live
       with. It names metrics rather than tables because nobody connects a warehouse to obtain
       tables. */}
   {justCompleted && (() => {
    const unlocked = metricsUnlockedBy(justCompleted.sourceType);
    if (unlocked.length === 0) return null;
    return (
     <div className="flex flex-wrap items-start gap-3 rounded-xl border border-primary/35 bg-primary-light/50 px-4 py-3.5 dark:bg-primary/10">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-white">
       <Sparkles size={17} />
      </div>
      <div className="min-w-0 flex-1">
       <p className="text-sm font-bold text-text-primary">
        {justCompleted.name} is modelled — {unlocked.length} new metrics you can measure
       </p>
       <p className="mt-0.5 text-sm text-text-secondary">
        Including {unlocked.slice(0, 3).join(", ")}
        {unlocked.length > 3 ? ` and ${unlocked.length - 3} more` : ""}.
       </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
       <button
        type="button"
        onClick={() =>
         navigate(`/kpis/discover?unlocked=${encodeURIComponent(justCompleted.sourceType)}`)
        }
        className="rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
       >
        Review them
       </button>
       <button
        type="button"
        onClick={() => setJustCompleted(null)}
        className="rounded-lg p-2 text-text-muted hover:bg-surface-secondary"
        aria-label="Dismiss"
       >
        <X size={16} />
       </button>
      </div>
     </div>
    );
   })()}

   {/* One line answering the only question that matters before anything else on this screen:
       can the product answer questions yet? */}
   {sources.length > 0 && (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-surface px-4 py-3.5">
     <div className="min-w-[240px]">
      <div className="flex flex-wrap items-center gap-2">
       <span className="text-sm font-bold text-text-primary">Workspace readiness</span>
       {readiness.reviewItems > 0 && (
        <span className="rounded-full bg-status-warning-light px-2 py-0.5 text-xs font-semibold text-status-warning">
         {readiness.reviewItems} decisions waiting
        </span>
       )}
       {readiness.building > 0 && (
        <span className="rounded-full bg-status-info-light px-2 py-0.5 text-xs font-semibold text-status-info">
         {readiness.building} still building
        </span>
       )}
      </div>
      <p className="mt-0.5 text-xs text-text-secondary">
       {readiness.modelled} of {readiness.total} sources modelled · {readiness.tables} tables
      </p>
     </div>
     <div className="min-w-[200px] flex-1 max-w-sm">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-tertiary">
       <div className="h-full rounded-full bg-primary" style={{ width: `${readiness.pct}%` }} />
      </div>
     </div>
     <button
      type="button"
      onClick={() => navigate("/kpis/discover")}
      className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
     >
      See what my data supports
     </button>
    </div>
   )}

   <div className="flex flex-wrap items-center gap-3">
    <CatalogSearchToolbar value={search} onValueChange={setSearch} placeholder="Search" />
    <div className="flex items-center gap-1 rounded-lg border border-border bg-surface-secondary p-0.5">
     {FILTERS.map((f) => {
      const count = f.id === "all" ? sources.length : sources.filter((s) => kindOf(s) === f.id).length;
      return (
       <button
        key={f.id}
        type="button"
        onClick={() => setFilter(f.id)}
        aria-pressed={filter === f.id}
        className={`rounded-md px-2.5 py-1.5 text-sm font-semibold transition-colors ${
         filter === f.id ? "bg-surface text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary"
        }`}
       >
        {f.label}
        <span className="ml-1.5 text-xs text-text-muted">{count}</span>
       </button>
      );
     })}
    </div>
    <div className="ml-auto flex items-center gap-2">
     <button
      type="button"
      onClick={() => navigate("/configure/external")}
      className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3.5 py-2 text-sm font-semibold text-text-secondary transition-colors hover:bg-surface-secondary"
     >
      <Link2 size={15} />
      Browse platforms
     </button>
     <button
      type="button"
      onClick={() => navigate("/configure/internal-sources/new")}
      className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
     >
      <Plus size={16} />
      Add database
     </button>
    </div>
   </div>

   {filtered.length === 0 && (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border-light bg-surface-secondary py-20 text-center">
     <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-border bg-surface">
      <Database size={28} className="text-text-secondary" />
     </div>
     <h3 className="text-lg font-bold text-text-primary">
      {search || filter !== "all" ? "Nothing matches" : "No sources connected"}
     </h3>
     <p className="mt-1.5 max-w-sm text-sm text-text-muted">
      {search || filter !== "all"
       ? "Try a different search or filter."
       : "Connect a database or a platform. As soon as one is connected we model it, and the metrics it supports appear under KPIs."}
     </p>
    </div>
   )}

   {filtered.length > 0 && (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
     {filtered.map((source) => (
      <SourceCard
       key={source.id}
       source={source}
       model={stateFor(source)}
       onSeeUnlocked={() => navigate(`/kpis/discover?unlocked=${encodeURIComponent(source.sourceType)}`)}
       onEdit={() => navigate(source.editPath)}
       onRemove={() => {
        if (window.confirm(`Remove "${source.name}"?`)) source.remove();
       }}
       onOpenSemanticLayer={() =>
        navigate(`/configure/semantic-layer?source=${encodeURIComponent(source.id)}`)
       }
      />
     ))}
    </div>
   )}
  </div>
 );
}
