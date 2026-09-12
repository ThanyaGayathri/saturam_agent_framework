// KPI-tile grid. Mirrors the look of the existing "playground-rich" panel so
// answers stay visually consistent across the demo.

import { ArrowUpRight, ArrowDownRight, Minus } from "lucide-react";
import { Card } from "../../../ui";
import BlockShell from "../BlockShell";

const DIRECTION_STYLES = {
  up: { icon: ArrowUpRight, color: "text-status-success" },
  down: { icon: ArrowDownRight, color: "text-status-critical" },
  flat: { icon: Minus, color: "text-text-muted" },
};

// The sentiment override controls the *change* colour independently of the
// numeric direction (e.g., "down" in error rate is a positive finding).
function resolveChangeColor(direction, sentiment) {
  if (sentiment === "positive") return "text-status-success";
  if (sentiment === "negative") return "text-status-critical";
  if (sentiment === "neutral") return "text-text-muted";
  return DIRECTION_STYLES[direction || "flat"]?.color || "text-text-muted";
}

export default function FindingsBlock({ block, onPinToggle }) {
  const items = block.items || [];
  if (!items.length) return null;

  return (
    <BlockShell
      block={block}
      title={block.title || "Key numbers"}
      subtitle="At a glance"
      onPinToggle={onPinToggle}
      bare
    >
      <div className={`grid gap-3 ${items.length >= 3 ? "grid-cols-3" : items.length === 2 ? "grid-cols-2" : "grid-cols-1"}`}>
        {items.map((f, i) => {
          const changeColor = resolveChangeColor(f.changeDirection, f.sentiment);
          const DirectionIcon = DIRECTION_STYLES[f.changeDirection || "flat"]?.icon;
          return (
            <Card
              key={i}
              className="p-4 dark:border-border dark:bg-surface-secondary dark:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]"
            >
              <p className="text-xs font-medium text-text-muted">{f.title}</p>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-xl font-semibold text-text-primary">{f.value}</span>
                {f.change ? (
                  <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${changeColor}`}>
                    {DirectionIcon ? <DirectionIcon size={12} aria-hidden /> : null}
                    {f.change}
                  </span>
                ) : null}
              </div>
              {f.desc ? (
                <p className="mt-2 text-xs text-text-secondary">{f.desc}</p>
              ) : null}
            </Card>
          );
        })}
      </div>
    </BlockShell>
  );
}
