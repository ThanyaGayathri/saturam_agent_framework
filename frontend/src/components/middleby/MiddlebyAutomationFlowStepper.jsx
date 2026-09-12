import { Check } from "lucide-react";

const STEPS = [
  { id: "analyze", label: "Analyze", caption: "Detect the signal" },
  { id: "monitor", label: "Monitor", caption: "Confirm context" },
  { id: "automate", label: "Automate", caption: "Exploratory demo" },
];

/**
 * In-flow stepper: advances inside the automation wizard (not external routes).
 */
export default function MiddlebyAutomationFlowStepper({
  activeIndex,
  maxReached,
  onSelect,
  compact = false,
}) {
  return (
    <nav aria-label="Automation flow progress" className={compact ? "" : "mb-8"}>
      <ol
        className={
          compact
            ? "flex flex-wrap items-center gap-2"
            : "grid gap-4 sm:grid-cols-3"
        }
      >
        {STEPS.map((step, index) => {
          const done = index < activeIndex || (index < maxReached && index !== activeIndex);
          const current = index === activeIndex;
          const reachable = index <= maxReached;
          const Icon = done && !current ? Check : null;

          const baseClass = compact
            ? "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold"
            : "flex w-full flex-col rounded-xl border px-4 py-3 text-left transition";

          let stateClass;
          if (compact) {
            stateClass = current
              ? "bg-primary text-white"
              : done
                ? "bg-status-success-light text-status-success"
                : "bg-surface-secondary text-text-muted";
          } else {
            stateClass = current
              ? "border-primary/50 bg-primary-light ring-2 ring-primary/20 dark:bg-primary/15"
              : done
                ? "border-status-success/40 bg-status-success-light/40"
                : "border-border bg-surface-default";
          }

          return (
            <li key={step.id} className={compact ? "" : "min-w-0"}>
              <button
                type="button"
                disabled={!reachable}
                onClick={() => reachable && onSelect?.(index)}
                className={`${baseClass} ${stateClass} ${
                  reachable ? "cursor-pointer hover:opacity-90" : "cursor-default opacity-60"
                }`}
                aria-current={current ? "step" : undefined}
              >
                <span className="inline-flex items-center gap-2">
                  <span
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                      current
                        ? "bg-primary text-white"
                        : done
                          ? "bg-status-success text-white"
                          : "bg-surface-secondary text-text-muted"
                    }`}
                  >
                    {Icon ? <Check className="h-3.5 w-3.5" aria-hidden /> : index + 1}
                  </span>
                  {!compact ? (
                    <span className="text-sm font-semibold text-text-primary">{step.label}</span>
                  ) : (
                    step.label
                  )}
                </span>
                {!compact ? (
                  <p className="mt-2 pl-8 text-xs text-text-muted">{step.caption}</p>
                ) : null}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
