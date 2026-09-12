// Executive-summary variant: one headline + up to three bullets. Used both
// as a standalone block and as the rendered form when "Quick summary" mode is
// active in the chat (Phase 2 toggle).

import { Sparkles } from "lucide-react";
import BlockShell from "../BlockShell";

export default function BriefBlock({ block, onPinToggle }) {
  const bullets = (block.bullets || []).slice(0, 3);

  return (
    <BlockShell
      block={block}
      title="Quick summary"
      subtitle="The one-minute read"
      onPinToggle={onPinToggle}
    >
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-light text-primary ring-1 ring-primary/20 dark:bg-primary/15"
          aria-hidden
        >
          <Sparkles size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-base font-semibold text-text-primary">{block.headline}</p>
          {bullets.length ? (
            <ul className="mt-2 space-y-1.5 text-sm text-text-secondary">
              {bullets.map((b, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </BlockShell>
  );
}
