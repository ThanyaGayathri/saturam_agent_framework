import { useState } from "react";
import { MessageSquarePlus, PanelLeftClose, PanelLeftOpen, Trash2, Pencil, Check, X } from "lucide-react";
import { groupConversations } from "../../storage/chat-conversations";

/**
 * The conversation rail: new chat, the list grouped by age, rename and delete.
 *
 * Collapsing leaves a narrow strip with the two controls that still make sense there, so the
 * transcript can have the full width on a small screen without the rail disappearing entirely.
 */
export default function ChatHistorySidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onRename,
  onDelete,
  collapsed,
  onToggleCollapsed,
}) {
  const [editingId, setEditingId] = useState(null);
  const [draftTitle, setDraftTitle] = useState("");
  const groups = groupConversations(conversations);

  const startRename = (row) => {
    setEditingId(row.id);
    setDraftTitle(row.title || "");
  };

  const commitRename = () => {
    if (editingId && draftTitle.trim()) onRename(editingId, draftTitle);
    setEditingId(null);
  };

  if (collapsed) {
    return (
      <div className="flex min-h-0 w-[56px] shrink-0 flex-col items-center gap-2 border-r border-border bg-surface-secondary/60 py-3">
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="rounded-lg p-2 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
          title="Show chat history"
          aria-label="Show chat history"
        >
          <PanelLeftOpen size={18} />
        </button>
        <button
          type="button"
          onClick={onNew}
          className="rounded-lg p-2 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
          title="New chat"
          aria-label="New chat"
        >
          <MessageSquarePlus size={18} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 w-[264px] shrink-0 flex-col border-r border-border bg-surface-secondary/60">
      <div className="flex items-center gap-2 px-3 py-3">
        <button
          type="button"
          onClick={onNew}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-primary shadow-sm transition hover:border-primary/40 hover:text-primary"
        >
          <MessageSquarePlus size={16} className="shrink-0 text-primary" />
          <span className="truncate">New chat</span>
        </button>
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="rounded-lg p-2 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
          title="Hide chat history"
          aria-label="Hide chat history"
        >
          <PanelLeftClose size={18} />
        </button>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" aria-label="Chat history">
        {groups.length === 0 ? (
          <p className="px-3 py-6 text-sm text-text-muted">
            Your conversations appear here once you ask something.
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.id} className="mb-3">
              <p className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                {group.label}
              </p>
              <ul className="space-y-0.5">
                {group.rows.map((row) => {
                  const active = row.id === activeId;
                  if (editingId === row.id) {
                    return (
                      <li key={row.id} className="flex items-center gap-1 px-1">
                        <input
                          autoFocus
                          value={draftTitle}
                          onChange={(e) => setDraftTitle(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") commitRename();
                            if (e.key === "Escape") setEditingId(null);
                          }}
                          className="min-w-0 flex-1 rounded-md border border-primary/40 bg-surface px-2 py-1.5 text-sm text-text-primary outline-none focus:ring-1 focus:ring-primary"
                          aria-label="Conversation title"
                        />
                        <button
                          type="button"
                          onClick={commitRename}
                          className="rounded p-1 text-primary hover:bg-surface-secondary"
                          aria-label="Save title"
                        >
                          <Check size={14} />
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingId(null)}
                          className="rounded p-1 text-text-muted hover:bg-surface-secondary"
                          aria-label="Cancel rename"
                        >
                          <X size={14} />
                        </button>
                      </li>
                    );
                  }
                  return (
                    <li key={row.id} className="group/row relative">
                      <button
                        type="button"
                        onClick={() => onSelect(row.id)}
                        className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 pr-14 text-left text-sm transition ${
                          active
                            ? "bg-primary-light font-semibold text-primary dark:bg-primary/15"
                            : "text-text-secondary hover:bg-surface-secondary hover:text-text-primary"
                        }`}
                        aria-current={active ? "true" : undefined}
                      >
                        <span className="truncate">{row.title || "New chat"}</span>
                      </button>
                      {/* Row actions stay out of the way until the row is hovered or focused,
                          so the list reads as titles rather than as a toolbar per line. */}
                      <span className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition group-hover/row:opacity-100 focus-within:opacity-100">
                        <button
                          type="button"
                          onClick={() => startRename(row)}
                          className="rounded p-1 text-text-muted transition hover:bg-surface hover:text-primary"
                          aria-label={`Rename ${row.title || "chat"}`}
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          type="button"
                          onClick={() => onDelete(row.id)}
                          className="rounded p-1 text-text-muted transition hover:bg-surface hover:text-status-critical"
                          aria-label={`Delete ${row.title || "chat"}`}
                        >
                          <Trash2 size={13} />
                        </button>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </nav>
    </div>
  );
}
