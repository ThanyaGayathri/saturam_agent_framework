// Shared rich assistant body for playground-style topic panels (demo UI).
import { useState } from "react";
import { ThumbsUp, ThumbsDown, Copy, RotateCcw, ArrowRight, Check } from "lucide-react";
import { Button, Card } from "../ui";
import { PLAYGROUND_TOPIC_PANELS } from "../../domain/playground-chat-replies";
import { MIDDLEBY_TOPIC_PANELS } from "../../domain/middleby-playground-chat";
import { MEDIA_TOPIC_PANELS } from "../../domain/media-playground-chat";

export default function PlaygroundRichReplyBody({ topic, panel: panelOverride, onBuildUseCase = undefined, text = "", onRetry }) {
  const [reaction, setReaction] = useState(null); // "up" | "down" | null
  const [copied, setCopied] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const handleCopy = () => {
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const handleRetry = async () => {
    if (!onRetry || retrying) return;
    setRetrying(true);
    try { await onRetry(); } finally { setRetrying(false); }
  };

  const toggleReaction = (dir) => setReaction((r) => (r === dir ? null : dir));
  const fallback =
    PLAYGROUND_TOPIC_PANELS[topic] ||
    MIDDLEBY_TOPIC_PANELS[topic] ||
    MEDIA_TOPIC_PANELS[topic] ||
    PLAYGROUND_TOPIC_PANELS.inventory;
  const panel =
    panelOverride?.findings?.length && panelOverride?.recommendations?.length ? panelOverride : fallback;

  return (
    <>
      <div className="grid grid-cols-3 gap-3">
        {panel.findings.map((f, i) => (
          <Card
            key={i}
            className="p-4 dark:border-border dark:bg-surface-secondary dark:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]"
          >
            <p className="text-xs font-medium text-text-muted">{f.title}</p>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-xl font-semibold text-text-primary">{f.value}</span>
              <span className="text-xs font-medium text-status-critical">{f.change}</span>
            </div>
            <p className="mt-2 text-xs text-text-secondary">{f.desc}</p>
          </Card>
        ))}
      </div>

      <Card className="p-4 dark:border-border dark:bg-surface-secondary dark:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]">
        <h4 className="text-sm font-semibold text-text-primary">Recommended Actions</h4>
        <ol className="mt-2 space-y-2">
          {panel.recommendations.map((rec, i) => (
            <li key={i} className="flex items-start gap-2 text-sm text-text-secondary">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary-light text-xs font-semibold text-primary ring-1 ring-primary/20 dark:bg-primary/20 dark:ring-primary/35">
                {i + 1}
              </span>
              {rec}
            </li>
          ))}
        </ol>
      </Card>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => toggleReaction("up")}
          className={`rounded p-1.5 transition hover:bg-surface-secondary ${reaction === "up" ? "text-primary" : "text-text-muted"}`}
          aria-label="Thumbs up"
          aria-pressed={reaction === "up"}
        >
          <ThumbsUp size={16} fill={reaction === "up" ? "currentColor" : "none"} />
        </button>
        <button
          type="button"
          onClick={() => toggleReaction("down")}
          className={`rounded p-1.5 transition hover:bg-surface-secondary ${reaction === "down" ? "text-primary" : "text-text-muted"}`}
          aria-label="Thumbs down"
          aria-pressed={reaction === "down"}
        >
          <ThumbsDown size={16} fill={reaction === "down" ? "currentColor" : "none"} />
        </button>
        <button
          type="button"
          onClick={handleCopy}
          className={`rounded p-1.5 transition hover:bg-surface-secondary ${copied ? "text-status-success" : "text-text-muted"}`}
          aria-label={copied ? "Copied" : "Copy"}
        >
          {copied ? <Check size={16} /> : <Copy size={16} />}
        </button>
        {onRetry && (
          <button
            type="button"
            onClick={handleRetry}
            disabled={retrying}
            className="rounded p-1.5 text-text-muted transition hover:bg-surface-secondary disabled:opacity-50"
            aria-label="Retry"
          >
            <RotateCcw size={16} className={retrying ? "animate-spin" : ""} />
          </button>
        )}
      </div>

      {typeof onBuildUseCase === "function" ? (
        <Button onClick={onBuildUseCase} className="w-full sm:w-auto">
          Open use case builder <ArrowRight size={14} className="inline" aria-hidden />
        </Button>
      ) : null}
    </>
  );
}
