import { ChevronDown } from "lucide-react";
import { TIER_LABELS, USE_CASE_TIERS } from "../../domain/use-case-catalog-seed";

const SELECT_CLASS =
  "min-w-[11rem] cursor-pointer appearance-none rounded-lg border border-border-light bg-surface py-2 pl-3 pr-9 text-sm font-medium text-text-primary shadow-sm transition hover:border-border focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6]";

/**
 * @param {{ value: string | null, onValueChange: (t: string | null) => void, id: string, labelText?: string }} props
 */
export function UseCaseTierFilterSelect({ value, onValueChange, id, labelText = "Filter by tier" }) {
  return (
    <div className="relative shrink-0">
      <label htmlFor={id} className="sr-only">
        {labelText}
      </label>
      <select
        id={id}
        value={value ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          onValueChange(v === "" ? null : v);
        }}
        className={SELECT_CLASS}
      >
        <option value="">All tiers</option>
        {USE_CASE_TIERS.map((t) => (
          <option key={t} value={t}>
            {TIER_LABELS[t]}
          </option>
        ))}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
        aria-hidden
        strokeWidth={2}
      />
    </div>
  );
}
