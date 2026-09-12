import { RotateCcw } from "lucide-react";

/**
 * Clears approval / completed state for an automation workflow so the user can run it again.
 */
export default function AutomationStartOverControl({
  onClick,
  compact = false,
  className = "",
}) {
  return (
    <div className={className}>
      <button
        type="button"
        onClick={onClick}
        className={
          compact
            ? "inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-default px-3 py-1.5 text-xs font-semibold text-text-primary hover:border-primary/40"
            : "inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
        }
      >
        <RotateCcw className="h-3.5 w-3.5" aria-hidden />
        Start over
      </button>
      <p className={`mt-1.5 text-text-muted ${compact ? "text-[11px]" : "text-xs"} max-w-md`}>
        Clears approval and results for this workflow so you can review actions and approve or run
        again. Does not change your charts or monitor data.
      </p>
    </div>
  );
}
