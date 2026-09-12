import { Target, ArrowUp, ArrowDown, Minus } from "lucide-react";

/**
 * "KPIs this automation targets" card.
 *
 * Renders a playbook's `kpiTargets` list so operators see which business
 * KPIs the run is designed to move, expected direction, horizon, and the
 * attribution note. Meant to be shown in the Delivered state below Impact.
 *
 * Props:
 *   kpiTargets: [{ name, contributionType, targetDelta: {value, horizon}, attribution }]
 */
export default function AutomationKpiTargets({ kpiTargets }) {
  if (!kpiTargets?.length) return null;
  return (
    <section
      className="rounded-xl border border-border bg-surface-default p-5 shadow-sm"
      aria-label="Business KPIs this automation targets"
    >
      <header className="mb-4 flex items-center gap-2">
        <Target className="h-4 w-4 text-primary" aria-hidden />
        <h3 className="text-sm font-semibold text-text-primary">
          KPIs this automation targets
        </h3>
      </header>
      <ul className="space-y-3">
        {kpiTargets.map((k, i) => (
          <li
            key={i}
            className="rounded-lg border border-border bg-surface-secondary/40 p-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <p className="text-sm font-semibold text-text-primary">{k.name}</p>
                <ContributionBadge type={k.contributionType} />
              </div>
              <div className="flex items-center gap-1.5 text-sm">
                <DirectionArrow value={k.targetDelta?.value} />
                <span className="font-semibold tabular-nums text-text-primary">
                  {k.targetDelta?.value || "-"}
                </span>
                {k.targetDelta?.horizon ? (
                  <span className="text-xs text-text-muted">
                    · {k.targetDelta.horizon}
                  </span>
                ) : null}
              </div>
            </div>
            {k.attribution ? (
              <p className="mt-2 text-xs leading-relaxed text-text-secondary">
                {k.attribution}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ContributionBadge({ type }) {
  if (type === "direct") {
    return (
      <span className="inline-flex rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary">
        Direct
      </span>
    );
  }
  if (type === "indirect") {
    return (
      <span className="inline-flex rounded-full bg-surface-secondary px-2 py-0.5 text-[10px] font-semibold text-text-secondary">
        Indirect
      </span>
    );
  }
  return null;
}

function DirectionArrow({ value }) {
  const s = String(value || "").trim();
  if (/^\+|up|increase|higher/i.test(s) && !/^-/.test(s)) {
    return <ArrowUp className="h-3.5 w-3.5 text-[#067647]" aria-hidden />;
  }
  if (/^-|down|reduce|lower/i.test(s)) {
    return <ArrowDown className="h-3.5 w-3.5 text-[#b42318]" aria-hidden />;
  }
  return <Minus className="h-3.5 w-3.5 text-text-muted" aria-hidden />;
}
