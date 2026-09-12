// Consistent visual wrapper used by every block. Centralises:
//   * "Add to report" pin so every block gets it for free
//   * "Why this?" info toggle that reveals the block's plain-language
//     explanation when the response generator provided one
//
// The explanation surface is how we deliver AI explainability at every step  - 
// audiences can drill from any block into "why did the AI show me this?"
// without leaving the chat.

import { useState } from "react";
import { HelpCircle } from "lucide-react";
import { Card } from "../../ui";

function ExplanationPanel({ text }) {
  return (
    <div className="mt-3 rounded-lg border border-primary/20 bg-primary-light/40 p-3 text-xs text-text-primary dark:bg-primary/10">
      <p className="mb-1 flex items-center gap-1 font-semibold text-primary">
        <HelpCircle size={12} />
        Why we're showing this
      </p>
      <p className="whitespace-pre-wrap text-text-secondary">{text}</p>
    </div>
  );
}

export default function BlockShell({
  block,
  title,
  subtitle,
  children,
  headerRight,
  padded = true,
  bare = false,
  // onPinToggle kept in signature for prop compatibility but no longer rendered
  //  -  "Add to report" is a single message-level control now, not per block.
  onPinToggle: _unusedPinToggle,
}) {
  const [explaining, setExplaining] = useState(false);
  const hasExplanation = Boolean(block?.explanation);

  const content = (
    <>
      {(title || subtitle || hasExplanation || headerRight) && (
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {title ? (
              <h4 className="text-sm font-semibold text-text-primary">{title}</h4>
            ) : null}
            {subtitle ? (
              <p className="mt-0.5 text-xs text-text-secondary">{subtitle}</p>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {headerRight}
            {hasExplanation ? (
              <button
                type="button"
                onClick={() => setExplaining((v) => !v)}
                aria-label={explaining ? "Hide explanation" : "Show why this is here"}
                aria-pressed={explaining}
                title="Why this?"
                className={`inline-flex items-center gap-1 rounded p-1.5 text-xs font-medium transition hover:bg-surface-secondary ${
                  explaining ? "text-primary" : "text-text-muted"
                }`}
              >
                <HelpCircle size={14} />
                <span className="hidden sm:inline">Why this?</span>
              </button>
            ) : null}
          </div>
        </div>
      )}
      {children ? <div className={title || subtitle ? "mt-3" : undefined}>{children}</div> : null}
      {hasExplanation && explaining ? <ExplanationPanel text={block.explanation} /> : null}
    </>
  );

  if (bare) {
    return <div>{content}</div>;
  }

  return (
    <Card
      className={`${padded ? "p-4" : ""} dark:border-border dark:bg-surface-secondary dark:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]`}
    >
      {content}
    </Card>
  );
}
