import { useEffect, useState } from "react";
import { AlertTriangle, Check, Pencil, RefreshCw, Target } from "lucide-react";

/**
 * The relevance question.
 *
 * Ranking suggestions "by relevance" needs a signal about the business, and on first run there
 * isn't one -- an untouched business hierarchy leaves ranking with nothing to sort on, so it
 * degrades to alphabetical at exactly the moment it should look deliberate. This asks once.
 *
 * The options are the deployment's own business areas, read from `GET /business-areas` when the
 * page loads -- not a list this browser made up. Each carries K9's verdict from the semantic
 * graph: an area is `supported` when some accepted concept in it has a confirmed table mapping.
 * Supported areas are the choices, shown with the evidence (how many concepts, how many tables);
 * unsupported ones are shown greyed with K9's reason, because "not offered" and "not supported
 * yet" are different things and the second one tells the customer what to fix.
 *
 * Deliberately inline rather than a blocking modal: a first-run modal is dismissed reflexively
 * and is then hard to find again. This collapses to a single editable line once answered, so the
 * answer stays visible and changeable while never blocking the page.
 *
 * `status` is the fetch: `loading` (skeleton), `ready` (the options), `error` (the graph or the
 * api could not answer -- said plainly, with a retry, and the page carries on unranked).
 */

function plural(n, word) {
 return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function Header({ children }) {
 return (
  <div className="flex items-start gap-3">
   <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-light dark:bg-primary/20">
    <Target size={18} className="text-primary" />
   </div>
   <div className="min-w-0 flex-1">
    <h3 className="text-base font-bold text-text-primary">Which areas of the business do you care about most?</h3>
    {children}
   </div>
  </div>
 );
}

const SHELL = "rounded-xl border border-primary/30 bg-primary-light/40 p-5 dark:bg-primary/10";

export default function KpiFocusPrompt({
 areas,
 selectedIds,
 onChange,
 status = "ready",
 error = null,
 onRetry,
}) {
 // `confirmed` is separate from "has a selection" on purpose: collapsing the moment the first
 // chip is picked makes it impossible to choose a second one. The panel closes when the user
 // says so, not when the first click lands. Seeded true when an answer was already stored, so a
 // returning user sees the collapsed line rather than being asked again.
 const [confirmed, setConfirmed] = useState(() => selectedIds.length > 0);
 const [editing, setEditing] = useState(false);
 // The stored ids are reconciled against the fetched areas after this mounts, and a focus kept
 // under an older vocabulary can reconcile to nothing. That must un-answer the question --
 // not merely reopen it -- or the first chip picked would collapse the panel again (the very
 // thing `confirmed` exists to prevent).
 useEffect(() => {
  if (selectedIds.length === 0) setConfirmed(false);
 }, [selectedIds.length]);
 const open = !confirmed || editing || selectedIds.length === 0;

 const supported = areas.filter((a) => a.supported);
 const unsupported = areas.filter((a) => !a.supported);

 const toggle = (id) =>
  onChange(selectedIds.includes(id) ? selectedIds.filter((x) => x !== id) : [...selectedIds, id]);

 if (status === "loading") {
  return (
   <div className={SHELL} role="status" aria-live="polite">
    <Header>
     <p className="mt-1 text-sm text-text-secondary">Checking which areas your connected data supports…</p>
     <div className="mt-4 flex flex-wrap gap-2">
      {["w-24", "w-20", "w-28", "w-24", "w-32", "w-20"].map((w, i) => (
       <span key={i} className={`h-8 ${w} animate-pulse rounded-full bg-primary-light`} />
      ))}
     </div>
    </Header>
   </div>
  );
 }

 if (status === "error") {
  const unavailable = error?.status === 503;
  return (
   <div className={SHELL} role="alert">
    <Header>
     <p className="mt-2 flex items-start gap-2 text-sm text-text-secondary">
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-warning" />
      <span>
       Couldn't ask the semantic layer which areas your data supports
       {unavailable ? " (the semantic layer is unavailable)" : ""} — showing every metric unranked.
       {error?.detail ? <span className="block text-xs text-text-muted">{error.detail}</span> : null}
      </span>
     </p>
     {onRetry && (
      <button
       type="button"
       onClick={onRetry}
       className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-border-light bg-surface px-3 py-1.5 text-xs font-semibold text-text-secondary transition-colors hover:bg-surface-secondary"
      >
       <RefreshCw size={12} />
       Retry
      </button>
     )}
    </Header>
   </div>
  );
 }

 // Answered and not being edited: one line, with a way back in.
 if (!open) {
  const names = areas.filter((a) => selectedIds.includes(a.id)).map((a) => a.label);
  return (
   <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-surface-secondary px-4 py-2.5 text-sm">
    <Target size={15} className="text-primary" />
    <span className="text-text-secondary">Ranked for</span>
    {names.map((n) => (
     <span key={n} className="rounded-full bg-primary-light px-2.5 py-0.5 text-xs font-semibold text-primary">
      {n}
     </span>
    ))}
    <button
     type="button"
     onClick={() => setEditing(true)}
     className="ml-auto inline-flex items-center gap-1.5 text-xs font-semibold text-primary hover:underline"
    >
     <Pencil size={12} />
     Change focus
    </button>
   </div>
  );
 }

 // "Show everything" means every area the data can actually rank on. When nothing is mapped
 // yet there is no such set, so it falls back to every area to keep the page openable, and
 // the note below says what to do about it.
 const everything = (supported.length ? supported : areas).map((a) => a.id);

 return (
  <div className={SHELL}>
   <Header>
    <p className="mt-1 text-sm text-text-secondary">
     We rank what your data can measure. Telling us where to look puts the metrics you actually
     report on at the top. You can change this at any time.
    </p>

    <div className="mt-4 flex flex-wrap gap-2">
     {areas.map((area) => {
      const on = selectedIds.includes(area.id);
      if (!area.supported) {
       return (
        <span
         key={area.id}
         role="button"
         aria-disabled="true"
         title={area.reason || "Not supported by the connected data yet."}
         className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-full border border-dashed border-border-light bg-surface px-3.5 py-1.5 text-sm font-semibold text-text-muted opacity-60"
        >
         {area.label}
        </span>
       );
      }
      return (
       <button
        key={area.id}
        type="button"
        onClick={() => toggle(area.id)}
        aria-pressed={on}
        title={area.reason || ""}
        className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-semibold transition-colors ${
         on
          ? "border-primary bg-primary text-white"
          : "border-border-light bg-surface text-text-secondary hover:border-primary hover:text-primary"
        }`}
       >
        {on && <Check size={13} />}
        {area.label}
        <span className={`text-xs font-medium ${on ? "text-white/80" : "text-text-muted"}`}>
         · {plural(area.concept_count ?? 0, "concept")} · {plural(area.table_count ?? 0, "table")}
        </span>
       </button>
      );
     })}
    </div>

    {/* K9's sentence is the same for every unsupported area, so it is said once, not per pill. */}
    {unsupported.length > 0 && (
     <p className="mt-3 text-xs text-text-muted">
      <span className="font-semibold text-text-secondary">Not yet supported:</span>{" "}
      {unsupported.map((a) => a.label).join(", ")}.{" "}
      {unsupported[0].reason || "No table is mapped to any concept in these areas."}
     </p>
    )}
    {supported.length === 0 && areas.length > 0 && (
     <p className="mt-2 text-xs text-text-muted">
      Nothing is mapped yet — accept mappings under Configure › Proposals to unlock ranking.
     </p>
    )}

    <div className="mt-4 flex flex-wrap items-center gap-3">
     <button
      type="button"
      disabled={selectedIds.length === 0}
      onClick={() => { setConfirmed(true); setEditing(false); }}
      className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40"
     >
      {editing ? "Update ranking" : "Show what my data supports"}
     </button>
     {/* Skipping is allowed; the consequence is stated rather than hidden. */}
     {!editing && (
      <button
       type="button"
       onClick={() => { onChange(everything); setConfirmed(true); }}
       className="text-sm font-semibold text-text-secondary hover:text-text-primary"
      >
       Show everything instead
      </button>
     )}
     {editing && (
      <button
       type="button"
       onClick={() => setEditing(false)}
       className="text-sm font-semibold text-text-secondary hover:text-text-primary"
      >
       Cancel
      </button>
     )}
    </div>
   </Header>
  </div>
 );
}
