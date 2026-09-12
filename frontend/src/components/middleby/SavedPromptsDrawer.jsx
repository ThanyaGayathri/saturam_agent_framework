// Slide-in list of questions the user has saved. Clicking Send re-runs the
// question in chat. The pencil edits the actual question text  -  no separate
// "label" concept, since that was confusing during earlier user feedback.

import { useState } from "react";
import { X, Bookmark, Send, Trash2, Pencil, Check } from "lucide-react";

function formatWhen(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function SavedPromptsDrawer({ open, onClose, prompts, onUse, onRemove, onUpdateText }) {
  const [editingId, setEditingId] = useState(null);
  const [draftText, setDraftText] = useState("");

  const startEdit = (p) => {
    setEditingId(p.id);
    setDraftText(p.text || "");
  };

  const cancelEdit = () => {
    setEditingId(null);
    setDraftText("");
  };

  const commitEdit = (p) => {
    if (!draftText.trim()) {
      cancelEdit();
      return;
    }
    onUpdateText?.(p.id, draftText);
    cancelEdit();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-label="Your saved questions">
      <button
        type="button"
        onClick={onClose}
        aria-label="Close saved questions"
        className="absolute inset-0 bg-black/30"
      />
      <aside className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col bg-surface-default shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
              <Bookmark size={16} />
              Your saved questions
            </h3>
            <p className="text-xs text-text-muted">Send any of these into the chat with one click.</p>
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

        <div className="flex-1 overflow-y-auto p-4">
          {!prompts?.length ? (
            <div className="rounded-lg border border-dashed border-border bg-surface-secondary/50 p-6 text-center">
              <p className="text-sm font-medium text-text-primary">Nothing saved yet</p>
              <p className="mt-1 text-xs text-text-secondary">
                In any chat answer, tap the bookmark to save the question that led to it.
              </p>
            </div>
          ) : (
            <ul className="space-y-2">
              {prompts.map((p) => (
                <li
                  key={p.id}
                  className="rounded-xl border border-border bg-surface-default p-3 transition hover:border-primary/40 hover:bg-primary-light/20"
                >
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      {editingId === p.id ? (
                        <div className="flex items-start gap-2">
                          <textarea
                            value={draftText}
                            onChange={(e) => setDraftText(e.target.value)}
                            rows={2}
                            placeholder="Edit the question"
                            className="w-full rounded border border-border bg-surface-default px-2 py-1 text-sm text-text-primary focus:border-primary focus:outline-none"
                            autoFocus
                            onKeyDown={(e) => {
                              if (e.key === "Enter" && !e.shiftKey) {
                                e.preventDefault();
                                commitEdit(p);
                              }
                              if (e.key === "Escape") {
                                e.preventDefault();
                                cancelEdit();
                              }
                            }}
                          />
                          <div className="flex flex-col gap-1">
                            <button
                              type="button"
                              onClick={() => commitEdit(p)}
                              aria-label="Save changes"
                              title="Save"
                              className="rounded p-1 text-primary hover:bg-primary-light/40"
                            >
                              <Check size={14} />
                            </button>
                            <button
                              type="button"
                              onClick={cancelEdit}
                              aria-label="Cancel"
                              title="Cancel"
                              className="rounded p-1 text-text-muted hover:bg-surface-secondary"
                            >
                              <X size={14} />
                            </button>
                          </div>
                        </div>
                      ) : (
                        <p className="text-sm text-text-primary">{p.text}</p>
                      )}
                      {editingId !== p.id ? (
                        <p className="mt-1 text-[11px] text-text-muted">
                          Saved {formatWhen(p.savedAt)}
                          {p.usageCount ? ` • Used ${p.usageCount}×` : ""}
                        </p>
                      ) : null}
                    </div>
                  </div>
                  {editingId !== p.id ? (
                    <div className="mt-2 flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => onUse?.(p)}
                        className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-xs font-medium text-white hover:bg-primary/90"
                      >
                        <Send size={12} />
                        Send this
                      </button>
                      <button
                        type="button"
                        onClick={() => startEdit(p)}
                        aria-label="Edit question"
                        title="Edit the question text"
                        className="rounded p-1.5 text-text-muted hover:bg-surface-secondary"
                      >
                        <Pencil size={12} />
                      </button>
                      <button
                        type="button"
                        onClick={() => onRemove?.(p.id)}
                        aria-label="Delete"
                        title="Remove from saved"
                        className="rounded p-1.5 text-text-muted hover:bg-status-critical/10 hover:text-status-critical"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}
