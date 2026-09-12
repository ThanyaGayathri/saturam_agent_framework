// Inline preview of the new answer format  -  opens as a modal from the home
// hub so users can see the layout without leaving the page they're on.

import { X, Sparkles } from "lucide-react";
import AnswerBlockRenderer from "../shared/answer-blocks/AnswerBlockRenderer";
import { buildBlocksPreviewCompactReply } from "../../domain/answer-blocks/preview-message";
import { EnterpriseProductMark } from "../shared/EnterpriseProductMark";

export default function AnswerFormatPreviewModal({ open, onClose, chatMode }) {
  if (!open) return null;

  const { meta } = buildBlocksPreviewCompactReply();

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-8" role="dialog" aria-modal>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close preview"
        className="fixed inset-0 bg-black/40"
      />
      <div className="relative z-10 my-8 flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-surface-default shadow-2xl">
        <header className="flex items-start justify-between border-b border-border px-5 py-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
              <Sparkles size={16} className="text-primary" />
              What a new-style answer looks like
            </h3>
            <p className="mt-0.5 text-xs text-text-secondary">
              A short example. Real answers pick and mix these cards based on your question.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1.5 text-text-muted transition hover:bg-surface-secondary"
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-5">
          <div className="flex w-full gap-3">
            <EnterpriseProductMark
              size="sm"
              shape="circle"
              className="mt-0.5 bg-surface shadow-sm ring-1 ring-border"
            />
            <div className="min-w-0 flex-1">
              <AnswerBlockRenderer meta={meta} chatMode={chatMode} />
            </div>
          </div>
        </div>

        <footer className="border-t border-border bg-surface-secondary/40 px-5 py-2.5 text-center">
          <p className="text-xs text-text-muted">
            Ask a real question in the chat to see the full version  -  with more charts, journey timelines, and drill-downs.
          </p>
        </footer>
      </div>
    </div>
  );
}
