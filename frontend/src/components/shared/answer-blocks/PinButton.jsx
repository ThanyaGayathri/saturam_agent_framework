// Small "Add to my report" toggle shown on every block. In Phase 1 this is
// purely a visual affordance — the actual Report drawer is built in Phase 4.
// We keep the button here so the demo already shows the affordance and users
// see the shape of the workflow.

import { useState } from "react";
import { Pin, PinOff } from "lucide-react";

export default function PinButton({ blockId, onToggle, initialPinned = false, className = "" }) {
  const [pinned, setPinned] = useState(initialPinned);

  const handleClick = () => {
    const next = !pinned;
    setPinned(next);
    if (typeof onToggle === "function") onToggle(blockId, next);
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={pinned ? "Remove from my report" : "Add to my report"}
      aria-pressed={pinned}
      title={pinned ? "Remove from my report" : "Add to my report"}
      className={`inline-flex items-center gap-1 rounded p-1.5 text-xs font-medium transition hover:bg-surface-secondary ${
        pinned ? "text-primary" : "text-text-muted"
      } ${className}`}
    >
      {pinned ? <PinOff size={14} /> : <Pin size={14} />}
      <span className="hidden sm:inline">{pinned ? "In my report" : "Add to report"}</span>
    </button>
  );
}
