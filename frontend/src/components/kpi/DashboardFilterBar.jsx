import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import { fetchDashboardFilterValues } from "../../services/dashboards-api";

/** `table.column` - the key shape `fetchDashboardData`'s `filters` option and the URL's `f`
 * search params both use. */
function keyFor(column) {
 return `${column.table}.${column.column}`;
}

/**
 * One filter's dropdown: a pill button ("column: All" / "column: N selected") that opens a
 * checkbox popover of that column's distinct values, staged locally until Apply so opening a
 * dropdown and closing it without touching anything never re-fetches the dashboard's data.
 * Values are fetched once, lazily, on first open (`GET /dashboards/{id}/filter-values`) and
 * cached for the life of this component.
 */
function FilterDropdown({ dashboardId, column, selected, onApply }) {
 const [open, setOpen] = useState(false);
 const [values, setValues] = useState(null); // null: not fetched yet
 const [loadError, setLoadError] = useState(null);
 const [staged, setStaged] = useState(selected);
 const rootRef = useRef(null);

 useEffect(() => {
  setStaged(selected);
 }, [selected]);

 useEffect(() => {
  if (!open) return;
  const close = (e) => {
   if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
  };
  document.addEventListener("mousedown", close);
  return () => document.removeEventListener("mousedown", close);
 }, [open]);

 const openDropdown = () => {
  setStaged(selected);
  setOpen(true);
  if (values !== null) return;
  fetchDashboardFilterValues(dashboardId, { table: column.table, column: column.column })
   .then((data) => setValues(data?.values || []))
   .catch((err) => setLoadError(err?.message || "Couldn't load values."));
 };

 const toggleValue = (value) => {
  setStaged((prev) =>
   prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
  );
 };

 const apply = () => {
  setOpen(false);
  onApply(staged);
 };

 const active = selected.length > 0;

 return (
  <div ref={rootRef} className="relative">
   <button
    type="button"
    onClick={() => (open ? setOpen(false) : openDropdown())}
    className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm font-medium ${
     active
      ? "border-primary bg-primary-light text-primary"
      : "border-border-light bg-surface text-text-secondary hover:bg-surface-secondary"
    }`}
   >
    {column.column}:
    {active ? (
     <span className="flex h-4 min-w-[16px] items-center justify-center rounded-full bg-primary px-1 text-[10px] font-bold text-white">
      {selected.length}
     </span>
    ) : (
     <span className="font-semibold">All</span>
    )}
    <ChevronDown size={13} />
   </button>

   {open && (
    <div className="absolute left-0 top-full z-20 mt-1.5 w-56 overflow-hidden rounded-lg border border-border-light bg-surface shadow-lg">
     <div className="max-h-60 overflow-y-auto p-1">
      {loadError ? (
       <p className="px-2.5 py-2 text-xs text-status-critical">{loadError}</p>
      ) : values === null ? (
       <p className="px-2.5 py-2 text-xs text-text-muted">Loading…</p>
      ) : values.length === 0 ? (
       <p className="px-2.5 py-2 text-xs text-text-muted">No values found.</p>
      ) : (
       values.map((value) => {
        const checked = staged.includes(value);
        return (
         <button
          key={value}
          type="button"
          onClick={() => toggleValue(value)}
          className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm ${
           checked ? "bg-primary-light font-semibold text-primary" : "text-text-primary hover:bg-surface-secondary"
          }`}
         >
          <span
           className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded ${
            checked ? "bg-primary" : "border border-border-light"
           }`}
          >
           {checked && <Check size={10} className="text-white" strokeWidth={3} />}
          </span>
          <span className="min-w-0 flex-1 truncate">{value}</span>
         </button>
        );
       })
      )}
     </div>
     <div className="flex items-center justify-end gap-2 border-t border-border px-2.5 py-2">
      <button
       type="button"
       onClick={() => setOpen(false)}
       className="rounded-md px-2 py-1 text-xs font-semibold text-text-secondary hover:text-text-primary"
      >
       Cancel
      </button>
      <button
       type="button"
       onClick={apply}
       className="rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-white hover:bg-primary-hover"
      >
       Apply
      </button>
     </div>
    </div>
   )}
  </div>
 );
}

/**
 * The dashboard's filter bar: one dropdown per `dashboard.filters` entry (R13.1's chosen
 * `{table, column}` pairs). `selected` is `{ "table.column": [value, ...] }`; a column left
 * out, or with an empty list, is "All" and is dropped from the query. Rendered only when the
 * dashboard actually declares filter columns, so a dashboard with none looks exactly as it did
 * before this feature.
 */
export default function DashboardFilterBar({ dashboardId, filters, selected, onChange }) {
 if (!filters || filters.length === 0) return null;

 const activeCount = Object.values(selected).filter((v) => v && v.length > 0).length;

 return (
  <div className="flex flex-wrap items-center gap-2 border-t border-surface-tertiary py-3">
   <span className="text-[11px] font-bold uppercase tracking-wide text-text-muted">Filters</span>
   {filters.map((column) => {
    const key = keyFor(column);
    return (
     <FilterDropdown
      key={key}
      dashboardId={dashboardId}
      column={column}
      selected={selected[key] || []}
      onApply={(values) => onChange({ ...selected, [key]: values })}
     />
    );
   })}
   {activeCount > 0 && (
    <button
     type="button"
     onClick={() => onChange({})}
     className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
    >
     <X size={12} />
     Clear filters
    </button>
   )}
  </div>
 );
}
