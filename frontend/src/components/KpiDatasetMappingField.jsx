import { useMemo } from "react";
import { Database } from "lucide-react";
import { groupDatasetsBySourceForProfile } from "../domain/kpi-data-catalog";
import { useDemoProfile } from "../context/DemoProfileContext";

/** Multi-select of enterprise source datasets for a KPI row (Add/Edit KPI). */
export default function KpiDatasetMappingField({ value, onChange }) {
  const { profileId } = useDemoProfile();
  const grouped = useMemo(() => groupDatasetsBySourceForProfile(profileId), [profileId]);
  const selected = Array.isArray(value) ? value : [];

  const toggle = (id) => {
    if (selected.includes(id)) onChange(selected.filter((x) => x !== id));
    else onChange([...selected, id]);
  };

  return (
    <div className="space-y-3">
      <label className="flex items-center gap-2 text-sm font-medium text-text-primary">
        <Database size={18} className="text-primary" aria-hidden />
        Enterprise source mappings
      </label>
      <p className="text-xs text-text-muted">
        Optional. Link this KPI to datasets in the KPI Gallery so teams can trace metrics to source tables.
      </p>
      <div className="max-h-56 space-y-4 overflow-y-auto rounded-lg border border-border bg-surface-secondary p-3">
        {grouped.map((g) => (
          <div key={g.sourceId}>
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
              {g.sourceLabel}
            </p>
            <ul className="space-y-2">
              {g.datasets.map((d) => (
                <li key={d.id}>
                  <label className="flex cursor-pointer items-start gap-2 text-sm text-text-primary">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 shrink-0 rounded border-border-light"
                      checked={selected.includes(d.id)}
                      onChange={() => toggle(d.id)}
                    />
                    <span>
                      <span className="font-mono text-xs text-text-secondary">{d.technicalName}</span>
                      <span className="mt-0.5 block text-xs text-text-muted">{d.description}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
