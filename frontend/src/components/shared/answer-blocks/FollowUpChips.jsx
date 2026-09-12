// Suggested next-prompt chips shown below an answer. Clicking a chip sends
// that prompt into the chat as if the user typed it.

import { Sparkles } from "lucide-react";

export default function FollowUpChips({ prompts = [], onSelect }) {
  if (!prompts.length) return null;
  return (
    <div className="mt-3">
      <p className="mb-2 flex items-center gap-1 text-xs font-semibold text-text-primary">
        <Sparkles size={12} />
        Click one to ask next
      </p>
      <div className="flex flex-wrap gap-2">
        {prompts.map((p, i) => (
          <button
            key={`${p}-${i}`}
            type="button"
            onClick={() => onSelect && onSelect(p)}
            className="rounded-full border border-primary/30 bg-primary-light/40 px-3 py-1 text-[13px] font-medium text-primary transition hover:border-primary/60 hover:bg-primary-light/70 dark:bg-primary/10 dark:hover:bg-primary/20"
            title="Send this as your next question"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}
