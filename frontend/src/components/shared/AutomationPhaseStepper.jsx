import { Check } from "lucide-react";

/**
 * Compact horizontal phase indicator for the Automate step.
 * Borrows visual grammar from UCBuilderLayout's left-rail stepper but stays
 * horizontal and lightweight because Automate is a runtime action, not a
 * multi-screen build-time flow.
 *
 * Props:
 *   phases: [{ id: string, label: string, sub?: string }]
 *   currentId: string  the active phase id
 *   doneIds:   string[] phases that have completed
 */
export default function AutomationPhaseStepper({ phases, currentId, doneIds = [] }) {
  return (
    <ol
      role="list"
      aria-label="Automation progress"
      className="flex flex-wrap items-center gap-y-2 rounded-xl border border-border bg-surface-secondary/40 px-4 py-3"
    >
      {phases.map((p, i) => {
        const isActive = p.id === currentId;
        const isDone = doneIds.includes(p.id);
        const nextExists = i < phases.length - 1;
        return (
          <li key={p.id} className="flex items-center gap-2">
            <div
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                isActive
                  ? "bg-primary text-white"
                  : isDone
                  ? "bg-primary/25 text-primary"
                  : "bg-surface-tertiary text-text-muted"
              }`}
              aria-current={isActive ? "step" : undefined}
            >
              {isDone ? <Check className="h-3 w-3" aria-hidden /> : i + 1}
            </div>
            <div className="mr-3 min-w-0">
              <p
                className={`text-[13px] leading-4 ${
                  isActive
                    ? "font-semibold text-primary"
                    : isDone
                    ? "font-medium text-text-primary"
                    : "font-medium text-text-muted"
                }`}
              >
                {p.label}
              </p>
              {p.sub ? <p className="text-[11px] text-text-muted">{p.sub}</p> : null}
            </div>
            {nextExists ? (
              <span
                className={`mr-3 hidden h-px w-8 sm:block ${
                  isDone ? "bg-primary/40" : "bg-border"
                }`}
                aria-hidden
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
