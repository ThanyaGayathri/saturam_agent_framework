/**
 * Animated "AI is working" loader.
 * Same visual grammar as the Use Case Builder Step 3 (AI Plan) entry loader.
 * Used by the Automate step Prepare phase to reinforce the AI just ran.
 */
export default function AiPreparingLoader({ message, tone = "primary" }) {
  const border = tone === "primary" ? "border-primary/35" : "border-border";
  const bg = tone === "primary" ? "bg-primary-light dark:bg-primary/15" : "bg-surface-secondary/40";
  const dot = tone === "primary" ? "bg-primary" : "bg-text-secondary";
  return (
    <div className={`flex items-center gap-3 rounded-xl border ${border} ${bg} px-4 py-3`}>
      <div className="flex items-center gap-1.5" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className={`h-2 w-2 rounded-full ${dot} animate-bounce`}
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </div>
      <p className="text-sm text-text-secondary">{message}</p>
    </div>
  );
}
