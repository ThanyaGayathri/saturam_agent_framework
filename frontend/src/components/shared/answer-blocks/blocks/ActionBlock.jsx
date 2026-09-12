// Row of clickable actions (drill-down chips, filter chips, external links).
// Each item announces its kind so the parent chat can react appropriately.

import { ArrowRight, Filter, ExternalLink, MessageSquare } from "lucide-react";
import BlockShell from "../BlockShell";

const KIND_ICONS = {
  drill: ArrowRight,
  filter: Filter,
  external: ExternalLink,
  prompt: MessageSquare,
};

const KIND_LABELS = {
  drill: "Explore this further",
  filter: "Narrow down",
  external: "Open outside app",
  prompt: "Ask about this",
};

export default function ActionBlock({ block, onPinToggle, onAction }) {
  const items = block.items || [];
  if (!items.length) return null;

  return (
    <BlockShell block={block} title="What you can do next" onPinToggle={onPinToggle} bare>
      <div className="flex flex-wrap gap-2">
        {items.map((it, i) => {
          const Icon = KIND_ICONS[it.kind] || ArrowRight;
          const tooltip = it.tooltip || KIND_LABELS[it.kind] || "Take an action";
          const isExternal = it.kind === "external" && it.payload?.href;
          const Component = isExternal ? "a" : "button";
          const componentProps = isExternal
            ? { href: it.payload.href, target: "_blank", rel: "noreferrer" }
            : {
                type: "button",
                onClick: () => onAction && onAction(it),
              };

          return (
            <Component
              key={i}
              {...componentProps}
              title={tooltip}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-default px-3 py-1.5 text-xs font-medium text-text-primary transition hover:border-primary/40 hover:bg-primary-light/30 hover:text-primary dark:bg-surface-secondary"
            >
              <Icon size={12} />
              {it.label}
            </Component>
          );
        })}
      </div>
    </BlockShell>
  );
}
