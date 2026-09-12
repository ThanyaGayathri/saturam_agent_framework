import { FileText, Paperclip, X } from "lucide-react";
import { formatAttachmentSize, openStoredAttachment } from "../../hooks/useChatAttachments";

/**
 * Horizontal list of pending (not yet sent) attachment chips shown above the
 * composer. Each chip has a remove button.
 */
export function PendingAttachmentChips({ staged, onRemove }) {
  if (!staged?.length) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {staged.map((s) => (
        <span
          key={s.localId}
          className="inline-flex max-w-[280px] items-center gap-1.5 rounded-full border border-border bg-primary-light dark:bg-primary/20 px-2.5 py-1 text-xs font-medium text-text-tertiary"
        >
          <Paperclip size={12} strokeWidth={2} aria-hidden className="shrink-0 text-primary" />
          <span className="truncate">{s.name}</span>
          <span className="shrink-0 text-text-muted">{formatAttachmentSize(s.size)}</span>
          <button
            type="button"
            onClick={() => onRemove(s.localId)}
            aria-label={`Remove ${s.name}`}
            className="ml-0.5 rounded-full p-0.5 text-text-muted hover:bg-surface hover:text-text-tertiary"
          >
            <X size={12} strokeWidth={2.5} aria-hidden />
          </button>
        </span>
      ))}
    </div>
  );
}

/**
 * Renders attachment chips for an already-sent chat message. Clicking a chip
 * opens the stored blob in a new tab.
 */
export function SentAttachmentChips({ attachments }) {
  if (!Array.isArray(attachments) || attachments.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {attachments.map((a) => (
        <button
          key={a.id}
          type="button"
          onClick={() => {
            void openStoredAttachment(a.id);
          }}
          title={`Open ${a.name}`}
          className="inline-flex max-w-[280px] items-center gap-1.5 rounded-full border border-white/40 bg-surface/60 px-2.5 py-1 text-xs font-medium text-text-primary transition hover:bg-surface"
        >
          <FileText size={12} strokeWidth={2} aria-hidden className="shrink-0 text-primary" />
          <span className="truncate">{a.name}</span>
          <span className="shrink-0 text-text-secondary">{formatAttachmentSize(a.size)}</span>
        </button>
      ))}
    </div>
  );
}
