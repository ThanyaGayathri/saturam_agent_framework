import { useMemo } from "react";
import { Link2 } from "lucide-react";

export default function KpiProcessMappingField({ processes, value, onChange }) {
  const sorted = useMemo(
    () => [...(processes || [])].sort((a, b) => (a.name || "").localeCompare(b.name || "")),
    [processes]
  );

  const toggle = (processId, checked) => {
    if (checked) onChange([...value, processId]);
    else onChange(value.filter((id) => id !== processId));
  };

  return (
    <div>
      <label className="mb-1.5 flex items-center gap-2 text-sm font-medium text-text-primary">
        <Link2 className="h-4 w-4 shrink-0 text-text-muted" strokeWidth={1.75} aria-hidden />
        Linked processes
      </label>
      <p className="mb-2 text-xs text-text-secondary">Choose which processes this KPI informs.</p>
      <div className="grid max-h-56 grid-cols-1 gap-2 overflow-y-auto rounded-lg border border-border-light bg-surface p-3 sm:grid-cols-2">
        {sorted.map((p) => (
          <label key={p.id} className="flex cursor-pointer items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 rounded border-border-light"
              checked={value.includes(p.id)}
              onChange={(e) => toggle(p.id, e.target.checked)}
            />
            <span className="text-text-primary">{p.name}</span>
          </label>
        ))}
      </div>
    </div>
  );
}
