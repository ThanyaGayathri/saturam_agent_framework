import { ArrowRight } from "lucide-react";

/**
 * One-line purpose for the active insight tab + optional next-step link.
 */
export default function InsightSessionViewHint({ purpose, next, onGoTo }) {
  if (!purpose) return null;

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface-secondary/40 px-4 py-2.5">
      <p className="text-sm text-text-secondary">{purpose}</p>
      {next && onGoTo ? (
        <button
          type="button"
          onClick={() => onGoTo(next.id)}
          className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-primary hover:underline"
        >
          {next.label}
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
