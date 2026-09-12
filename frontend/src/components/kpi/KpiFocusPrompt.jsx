import { useState } from "react";
import { Check, Pencil, Target } from "lucide-react";

/**
 * The relevance question.
 *
 * Ranking suggestions "by relevance" needs a signal about the business, and on first run there
 * isn't one -- an untouched business hierarchy leaves ranking with nothing to sort on, so it
 * degrades to alphabetical at exactly the moment it should look deliberate. This asks once.
 *
 * Deliberately inline rather than a blocking modal: a first-run modal is dismissed reflexively
 * and is then hard to find again. This collapses to a single editable line once answered, so the
 * answer stays visible and changeable while never blocking the page.
 */
export default function KpiFocusPrompt({ areas, selectedIds, onChange }) {
 // `confirmed` is separate from "has a selection" on purpose: collapsing the moment the first
 // chip is picked makes it impossible to choose a second one. The panel closes when the user
 // says so, not when the first click lands. Seeded true when an answer was already stored, so a
 // returning user sees the collapsed line rather than being asked again.
 const [confirmed, setConfirmed] = useState(() => selectedIds.length > 0);
 const [editing, setEditing] = useState(false);
 const open = !confirmed || editing;

 const toggle = (id) =>
  onChange(selectedIds.includes(id) ? selectedIds.filter((x) => x !== id) : [...selectedIds, id]);

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

 return (
  <div className="rounded-xl border border-primary/30 bg-primary-light/40 p-5 dark:bg-primary/10">
   <div className="flex items-start gap-3">
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-light dark:bg-primary/20">
     <Target size={18} className="text-primary" />
    </div>
    <div className="min-w-0 flex-1">
     <h3 className="text-base font-bold text-text-primary">Which areas of the business do you care about most?</h3>
     <p className="mt-1 text-sm text-text-secondary">
      We rank what your data can measure. Telling us where to look puts the metrics you actually
      report on at the top. You can change this at any time.
     </p>

     <div className="mt-4 flex flex-wrap gap-2">
      {areas.map((area) => {
       const on = selectedIds.includes(area.id);
       return (
        <button
         key={area.id}
         type="button"
         onClick={() => toggle(area.id)}
         aria-pressed={on}
         className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-semibold transition-colors ${
          on
           ? "border-primary bg-primary text-white"
           : "border-border-light bg-surface text-text-secondary hover:border-primary hover:text-primary"
         }`}
        >
         {on && <Check size={13} />}
         {area.label}
        </button>
       );
      })}
     </div>

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
        onClick={() => { onChange(areas.map((a) => a.id)); setConfirmed(true); }}
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
    </div>
   </div>
  </div>
 );
}
