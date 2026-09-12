// Numbered list of suggested next actions. Matches the "Recommended Actions"
// pattern used by PlaygroundRichReplyBody so the demo feels consistent.

import BlockShell from "../BlockShell";

const PRIORITY_STYLES = {
  high: "border-status-critical/30 bg-status-critical/5 text-status-critical",
  medium: "border-status-warning/30 bg-status-warning/5 text-status-warning",
  low: "border-border bg-surface-secondary text-text-muted",
};

function normalise(item) {
  if (typeof item === "string") return { text: item };
  return item || { text: "" };
}

export default function RecommendationBlock({ block, onPinToggle }) {
  const items = (block.items || []).map(normalise).filter((it) => it.text);
  if (!items.length) return null;

  return (
    <BlockShell
      block={block}
      title={block.title || "What to try next"}
      onPinToggle={onPinToggle}
    >
      <ol className="space-y-2">
        {items.map((rec, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-text-secondary">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary-light text-xs font-semibold text-primary ring-1 ring-primary/20 dark:bg-primary/20 dark:ring-primary/35">
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-text-primary">{rec.text}</p>
              {rec.rationale ? (
                <p className="mt-0.5 text-xs text-text-secondary">{rec.rationale}</p>
              ) : null}
              {rec.priority ? (
                <span
                  className={`mt-1 inline-block rounded-full border px-2 py-0.5 text-[10px] font-medium ${PRIORITY_STYLES[rec.priority] || PRIORITY_STYLES.low}`}
                  title="How urgent this is"
                >
                  {rec.priority === "high" ? "Do this first" : rec.priority === "medium" ? "Worth doing" : "Nice to have"}
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </BlockShell>
  );
}
