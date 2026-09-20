import { useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";

/**
 * The "+ Add metric" affordance for the manual (and AI-editable) builder: a dashed button that
 * opens an inline searchable list of the deployment's real, `ready` KPIs (`readyKpis`, from
 * `GET /kpis`) - the same set a tile can legally reference (`_check_tiles_ready` on the
 * backend). Already-selected identifiers are excluded so nothing can be added twice.
 */
export default function MetricPicker({ readyKpis, selectedIdentifiers, onAdd, loading = false }) {
 const [open, setOpen] = useState(false);
 const [query, setQuery] = useState("");

 const options = useMemo(() => {
  const q = query.trim().toLowerCase();
  return readyKpis
   .filter((kpi) => !selectedIdentifiers.has(kpi.identifier))
   .filter((kpi) => !q || kpi.name.toLowerCase().includes(q) || kpi.identifier.toLowerCase().includes(q))
   .slice(0, 30);
 }, [readyKpis, selectedIdentifiers, query]);

 if (!open) {
  return (
   <button
    type="button"
    onClick={() => setOpen(true)}
    className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-border-light px-3 py-1.5 text-xs font-semibold text-text-secondary hover:border-primary/40 hover:text-primary"
   >
    <Plus size={13} />
    Add metric
   </button>
  );
 }

 return (
  <div className="rounded-lg border border-border shadow-sm">
   <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-text-muted">
    <Search size={14} />
    <input
     autoFocus
     value={query}
     onChange={(e) => setQuery(e.target.value)}
     // In the builder this input sits inside the dashboard form, whose submit button is enabled
     // as soon as a KPI-page selection has pre-filled the charts, so Enter while searching would
     // create the dashboard. Enter only filters here; picking a KPI is a click.
     onKeyDown={(e) => {
      if (e.key === "Enter") e.preventDefault();
     }}
     placeholder="Search KPIs…"
     className="w-full bg-transparent text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
    />
    <button
     type="button"
     onClick={() => {
      setOpen(false);
      setQuery("");
     }}
     className="text-xs font-semibold text-text-secondary hover:text-text-primary"
    >
     Done
    </button>
   </div>
   <div className="max-h-56 overflow-y-auto p-1.5">
    {options.length === 0 ? (
     <p className="px-2 py-3 text-center text-xs text-text-muted">
      {readyKpis.length === 0
       ? loading
        ? "Loading KPIs..."
        : "No registered, ready KPIs yet."
       : "No matches."}
     </p>
    ) : (
     options.map((kpi) => (
      <button
       key={kpi.identifier}
       type="button"
       onClick={() => onAdd(kpi)}
       className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-text-primary hover:bg-surface-secondary"
      >
       <span className="truncate">{kpi.name}</span>
       {kpi.unit ? (
        <span className="ml-auto shrink-0 text-xs text-text-muted">{kpi.unit}</span>
       ) : null}
      </button>
     ))
    )}
   </div>
  </div>
 );
}
