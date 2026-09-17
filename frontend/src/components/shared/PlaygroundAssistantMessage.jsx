// Assistant bubble layout shared by Playground active chat and Use Case builder chat.
import { useState } from "react";
import { Copy, Check, RotateCcw, ThumbsUp, ThumbsDown, Bookmark, Calendar, Pin } from "lucide-react";
import PlaygroundRichReplyBody from "./PlaygroundRichReplyBody";
import { EnterpriseProductMark } from "./EnterpriseProductMark";
import { AnswerBlockRenderer } from "./answer-blocks";
import { isAnswerBlockMeta } from "../../domain/answer-blocks/schema";
import MarkdownProse from "./answer-blocks/MarkdownProse";

export default function PlaygroundAssistantMessage({ msg, onBuildUseCase, onRetry, onFollowUpSelect, onBlockAction, onPinToggle, onSaveMessage, onScheduleMessage, onAddMessageToReport, isMessagePinned = false, activeReportTitle, chatMode, isLatestAssistant = true, children }) {
  const isAnswerBlock = isAnswerBlockMeta(msg.meta);
  const [pinFlash, setPinFlash] = useState(false);

  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState(null);

  const handleCopy = () => {
    navigator.clipboard?.writeText(msg.text || "").then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  /** Match `PlaygroundRichReplyBody` feedback row: 16px icons, p-1.5, fill on active thumbs. */
  const actionIdle = "text-text-muted";
  const actionHover = "rounded p-1.5 transition hover:bg-surface-secondary";

  return (
    <div className="group flex w-full gap-3">
      <EnterpriseProductMark
        size="sm"
        shape="circle"
        className="mt-0.5 bg-surface shadow-sm ring-1 ring-border"
      />

      <div className="min-w-0 flex-1 space-y-3">
        {/* When rendering an answer-block message the intro paragraph lives inside
            the renderer, so we skip the plain-text bubble to avoid duplication. */}
        {!isAnswerBlock ? (
          <div className="text-[15px] leading-relaxed text-text-primary">
            <MarkdownProse>{msg.text}</MarkdownProse>
          </div>
        ) : null}

        {msg.meta?.variant === "playground-rich" ? (
          <PlaygroundRichReplyBody
            topic={msg.meta?.topic}
            panel={msg.meta?.panel}
            onBuildUseCase={onBuildUseCase}
            text={msg.text ?? ""}
            onRetry={onRetry}
          />
        ) : null}

        {isAnswerBlock ? (
          <AnswerBlockRenderer
            meta={msg.meta}
            chatMode={chatMode}
            hideFollowUps={!isLatestAssistant}
            onFollowUpSelect={onFollowUpSelect}
            onAction={onBlockAction}
            onPinToggle={onPinToggle}
          />
        ) : null}

        {children}

        {msg.meta?.variant !== "playground-rich" && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setFeedback((v) => (v === "up" ? null : "up"))}
              aria-label="Thumbs up"
              aria-pressed={feedback === "up"}
              className={`${actionHover} ${feedback === "up" ? "text-primary" : actionIdle}`}
            >
              <ThumbsUp size={16} fill={feedback === "up" ? "currentColor" : "none"} />
            </button>
            <button
              type="button"
              onClick={() => setFeedback((v) => (v === "down" ? null : "down"))}
              aria-label="Thumbs down"
              aria-pressed={feedback === "down"}
              className={`${actionHover} ${feedback === "down" ? "text-primary" : actionIdle}`}
            >
              <ThumbsDown size={16} fill={feedback === "down" ? "currentColor" : "none"} />
            </button>
            <button
              type="button"
              onClick={handleCopy}
              aria-label={copied ? "Copied" : "Copy"}
              className={`${actionHover} ${copied ? "text-status-success" : actionIdle}`}
            >
              {copied ? <Check size={16} /> : <Copy size={16} />}
            </button>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                aria-label="Retry"
                title="Regenerate"
                className={`${actionHover} ${actionIdle}`}
              >
                <RotateCcw size={16} />
              </button>
            )}
            {onSaveMessage && (
              <button
                type="button"
                onClick={onSaveMessage}
                aria-label="Save this question"
                title="Save this question so you can send it again later"
                className={`${actionHover} ${actionIdle}`}
              >
                <Bookmark size={16} />
              </button>
            )}
            {onScheduleMessage && (
              <button
                type="button"
                onClick={onScheduleMessage}
                aria-label="Send this to me on a schedule"
                title="Send this to me on a schedule"
                className={`${actionHover} ${actionIdle}`}
              >
                <Calendar size={16} />
              </button>
            )}
            {isAnswerBlock && onAddMessageToReport ? (
              <button
                type="button"
                onClick={() => {
                  onAddMessageToReport();
                  setPinFlash(true);
                  setTimeout(() => setPinFlash(false), 1500);
                }}
                aria-label="Add this whole answer to your report"
                title={activeReportTitle ? `Add this answer to "${activeReportTitle}"` : "Add this answer to your report"}
                className={`ml-1 inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition ${
                  pinFlash || isMessagePinned
                    ? "border-primary bg-primary-light/40 text-primary"
                    : "border-border bg-surface-default text-text-secondary hover:border-primary/40 hover:text-primary"
                }`}
              >
                {pinFlash || isMessagePinned ? <Check size={12} /> : <Pin size={12} />}
                {pinFlash ? "Added" : isMessagePinned ? "In report" : "Add to report"}
              </button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
