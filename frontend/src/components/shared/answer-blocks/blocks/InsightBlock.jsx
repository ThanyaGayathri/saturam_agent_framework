// A short headline finding with optional supporting text.
// Meant to lead an answer  -  "Here's what stands out".

import { TrendingUp, TrendingDown, Info } from "lucide-react";
import BlockShell from "../BlockShell";

const SENTIMENT_STYLES = {
  positive: {
    ring: "ring-status-success/25",
    tint: "bg-status-success/10",
    text: "text-status-success",
    icon: TrendingUp,
  },
  negative: {
    ring: "ring-status-critical/25",
    tint: "bg-status-critical/10",
    text: "text-status-critical",
    icon: TrendingDown,
  },
  neutral: {
    ring: "ring-primary/25",
    tint: "bg-primary-light/60 dark:bg-primary/15",
    text: "text-primary",
    icon: Info,
  },
};

export default function InsightBlock({ block, onPinToggle }) {
  const sentiment = block.sentiment || "neutral";
  const style = SENTIMENT_STYLES[sentiment] || SENTIMENT_STYLES.neutral;
  const Icon = style.icon;

  return (
    <BlockShell block={block} onPinToggle={onPinToggle}>
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ring-1 ${style.ring} ${style.tint} ${style.text}`}
          aria-hidden
        >
          <Icon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-text-primary">{block.headline}</p>
          {block.subtext ? (
            <p className="mt-1 text-sm text-text-secondary">{block.subtext}</p>
          ) : null}
        </div>
      </div>
    </BlockShell>
  );
}
