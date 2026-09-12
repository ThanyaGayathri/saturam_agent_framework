// Multi-report drawer. Users have named reports (e.g. "Weekly exec brief",
// "TurboChef deep dive"). One is active  -  "Add to report" pins to the active
// report. From here users switch which report is active, create new ones,
// rename, delete, and export the current one to PowerPoint / PDF / email.

import { useEffect, useMemo, useState } from "react";
import {
  X, FileDown, Presentation, Mail, Trash2, FileText, Loader2, Plus, Pencil, ChevronLeft, CheckCircle2,
} from "lucide-react";
import {
  exportBlocksAsPptx,
  exportBlocksAsPdf,
  exportBlocksAsEmailText,
  downloadBlob,
} from "../../domain/answer-blocks/export-service";

function truncate(text, n = 80) {
  if (!text) return "";
  return text.length > n ? `${text.slice(0, n).trim()}…` : text;
}

function blockPreviewText(block) {
  if (block.type === "insight") return block.headline;
  if (block.type === "brief") return block.headline;
  if (block.type === "findings") return `${(block.items || []).length} key numbers`;
  if (block.type === "chart") return block.title || "Chart";
  if (block.type === "table") return `${block.title || "Table"} • ${(block.rows || []).length} rows`;
  if (block.type === "recommendation") return `${(block.items || []).length} recommendations`;
  if (block.type === "journey") return block.title || "Customer journey";
  if (block.type === "citation") return "Where the data came from";
  return block.type || "Item";
}

function blockLabel(block) {
  const map = {
    insight: "Highlight",
    brief: "Quick summary",
    findings: "Key numbers",
    chart: "Chart",
    table: "Table",
    recommendation: "Actions",
    journey: "Customer journey",
    citation: "Data sources",
    action: "Actions",
  };
  return map[block.type] || block.type;
}

function formatWhen(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// ---------- List view (all reports) --------------------------------------

function ReportsListView({ reports, activeReportId, onOpen, onSetActive, onDelete, onCreate }) {
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");

  const startCreate = () => {
    setNewTitle("Weekly report");
    setCreating(true);
  };
  const commitCreate = () => {
    const title = newTitle.trim();
    if (!title) return setCreating(false);
    onCreate(title);
    setCreating(false);
    setNewTitle("");
  };

  return (
    <div className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs text-text-secondary">
          Each report is a separate deck you can build up over time. The one marked with a filled dot is active  -  new pins go there.
        </p>
      </div>

      {creating ? (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-primary/40 bg-primary-light/30 p-2 dark:bg-primary/10">
          <input
            type="text"
            value={newTitle}
            autoFocus
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); commitCreate(); }
              if (e.key === "Escape") setCreating(false);
            }}
            placeholder="e.g., Weekly exec brief"
            className="flex-1 rounded border border-border bg-surface-default px-2 py-1 text-sm text-text-primary focus:border-primary focus:outline-none"
          />
          <button
            type="button"
            onClick={commitCreate}
            className="rounded bg-primary px-2 py-1 text-xs font-medium text-white hover:bg-primary/90"
          >
            Create
          </button>
          <button
            type="button"
            onClick={() => setCreating(false)}
            className="rounded p-1 text-text-muted hover:bg-surface-secondary"
            aria-label="Cancel"
          >
            <X size={12} />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={startCreate}
          className="mb-3 inline-flex items-center gap-1.5 rounded-lg border border-dashed border-primary/40 bg-primary-light/20 px-3 py-2 text-xs font-medium text-primary transition hover:border-primary/60 hover:bg-primary-light/40 dark:bg-primary/10"
        >
          <Plus size={12} />
          Start a new report
        </button>
      )}

      <ul className="space-y-2">
        {reports.map((r) => {
          const isActive = r.id === activeReportId;
          return (
            <li
              key={r.id}
              className={`rounded-xl border p-3 transition ${
                isActive
                  ? "border-primary/40 bg-primary-light/30 dark:bg-primary/10"
                  : "border-border bg-surface-default hover:border-primary/30"
              }`}
            >
              <div className="flex items-start gap-2">
                <button
                  type="button"
                  onClick={() => onOpen(r.id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold text-text-primary">{r.title}</p>
                    {isActive ? (
                      <span className="inline-flex items-center gap-0.5 rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-white">
                        <CheckCircle2 size={9} />
                        Adding here
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-0.5 text-[11px] text-text-muted">
                    {r.items.length} item{r.items.length === 1 ? "" : "s"}
                    {" · "}Updated {formatWhen(r.updatedAt)}
                  </p>
                </button>
                {!isActive ? (
                  <button
                    type="button"
                    onClick={() => onSetActive(r.id)}
                    title="Make this the report where new pins go"
                    className="rounded-full border border-border bg-surface-default px-2 py-0.5 text-[10px] font-medium text-text-secondary hover:border-primary/40 hover:text-primary"
                  >
                    Add here
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => onDelete(r.id)}
                  aria-label="Delete this report"
                  title="Delete this report"
                  className="rounded p-1 text-text-muted hover:bg-status-critical/10 hover:text-status-critical"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------- Detail view (one report + export) ----------------------------

function ReportDetailView({ report, onBack, onRename, onUnpin, onClear }) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(report?.title || "");
  const [busy, setBusy] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setTitleDraft(report?.title || "");
  }, [report?.id, report?.title]);

  if (!report) return null;

  const commitRename = () => {
    const t = titleDraft.trim();
    if (t && t !== report.title) onRename(report.id, t);
    setEditingTitle(false);
  };

  const filename = (report.title || "report").replace(/[^\w\s-]/g, "").trim() || "report";

  const handlePptx = async () => {
    setBusy("pptx");
    try {
      const blob = await exportBlocksAsPptx(report.items, { title: report.title });
      if (blob) downloadBlob(blob, `${filename}.pptx`);
    } catch (e) { console.error("[report] pptx failed", e); }
    finally { setBusy(null); }
  };

  const handlePdf = () => {
    setBusy("pdf");
    try {
      const blob = exportBlocksAsPdf(report.items, { title: report.title });
      if (blob) downloadBlob(blob, `${filename}.pdf`);
    } catch (e) { console.error("[report] pdf failed", e); }
    finally { setBusy(null); }
  };

  const handleEmail = async () => {
    setBusy("email");
    try {
      const text = exportBlocksAsEmailText(report.items, { title: report.title });
      await navigator.clipboard?.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) { console.error("[report] email failed", e); }
    finally { setBusy(null); }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <button
          type="button"
          onClick={onBack}
          aria-label="Back to reports list"
          className="rounded p-1 text-text-muted hover:bg-surface-secondary"
          title="Back"
        >
          <ChevronLeft size={16} />
        </button>
        {editingTitle ? (
          <input
            type="text"
            value={titleDraft}
            autoFocus
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); commitRename(); }
              if (e.key === "Escape") { setTitleDraft(report.title); setEditingTitle(false); }
            }}
            className="flex-1 rounded border border-border bg-surface-default px-2 py-1 text-sm font-semibold text-text-primary focus:border-primary focus:outline-none"
          />
        ) : (
          <>
            <p className="flex-1 truncate text-sm font-semibold text-text-primary">{report.title}</p>
            <button
              type="button"
              onClick={() => setEditingTitle(true)}
              aria-label="Rename this report"
              title="Rename"
              className="rounded p-1 text-text-muted hover:bg-surface-secondary"
            >
              <Pencil size={12} />
            </button>
          </>
        )}
      </div>

      {report.items.length ? (
        <div className="border-b border-border p-3">
          <div className="grid grid-cols-3 gap-2">
            <button
              type="button"
              onClick={handlePptx}
              disabled={busy != null}
              className="inline-flex flex-col items-center gap-1 rounded-lg border border-border bg-surface-default p-2 text-xs font-medium text-text-primary transition hover:border-primary/40 hover:text-primary disabled:opacity-60"
            >
              {busy === "pptx" ? <Loader2 size={16} className="animate-spin" /> : <Presentation size={16} />}
              PowerPoint
            </button>
            <button
              type="button"
              onClick={handlePdf}
              disabled={busy != null}
              className="inline-flex flex-col items-center gap-1 rounded-lg border border-border bg-surface-default p-2 text-xs font-medium text-text-primary transition hover:border-primary/40 hover:text-primary disabled:opacity-60"
            >
              {busy === "pdf" ? <Loader2 size={16} className="animate-spin" /> : <FileDown size={16} />}
              PDF
            </button>
            <button
              type="button"
              onClick={handleEmail}
              disabled={busy != null}
              className="inline-flex flex-col items-center gap-1 rounded-lg border border-border bg-surface-default p-2 text-xs font-medium text-text-primary transition hover:border-primary/40 hover:text-primary disabled:opacity-60"
            >
              {busy === "email" ? <Loader2 size={16} className="animate-spin" /> : <Mail size={16} />}
              {copied ? "Copied ✓" : "For email"}
            </button>
          </div>
        </div>
      ) : null}

      <div className="flex-1 overflow-y-auto p-4">
        {!report.items.length ? (
          <div className="rounded-lg border border-dashed border-border bg-surface-secondary/50 p-6 text-center">
            <p className="text-sm font-medium text-text-primary">This report is empty</p>
            <p className="mt-1 text-xs text-text-secondary">
              Make this report the active one, then click "Add to report" on any card in a chat answer to collect it here.
            </p>
          </div>
        ) : (
          <ul className="space-y-2">
            {report.items.map((item) => (
              <li key={item.id} className="rounded-xl border border-border bg-surface-default p-3">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-primary">
                      {blockLabel(item.block)}
                    </p>
                    <p className="mt-0.5 text-sm text-text-primary">
                      {truncate(blockPreviewText(item.block), 90)}
                    </p>
                    {item.sourceQuestion ? (
                      <p className="mt-1 text-[11px] italic text-text-muted">
                        From: {truncate(item.sourceQuestion, 80)}
                      </p>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => onUnpin(report.id, item.block.id)}
                    aria-label="Remove from report"
                    title="Remove from this report"
                    className="rounded p-1 text-text-muted hover:bg-status-critical/10 hover:text-status-critical"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {report.items.length ? (
        <footer className="flex items-center justify-between border-t border-border bg-surface-secondary/40 px-4 py-2">
          <p className="text-[11px] text-text-muted">
            {report.items.length} item{report.items.length === 1 ? "" : "s"}
          </p>
          <button
            type="button"
            onClick={() => onClear(report.id)}
            className="text-[11px] font-medium text-status-critical hover:underline"
          >
            Empty this report
          </button>
        </footer>
      ) : null}
    </div>
  );
}

// ---------- Public component ---------------------------------------------

export default function ReportDrawer({
  open,
  onClose,
  reports,
  activeReportId,
  onSetActive,
  onCreateReport,
  onRenameReport,
  onDeleteReport,
  onUnpin,
  onClear,
}) {
  const [openReportId, setOpenReportId] = useState(null);

  // Whenever the drawer opens, land on the list view (unless the user was
  // deep-linking into a specific report by way of "Your report" chip).
  useEffect(() => {
    if (open) setOpenReportId(null);
  }, [open]);

  const openReport = useMemo(
    () => (openReportId ? reports.find((r) => r.id === openReportId) : null),
    [openReportId, reports]
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-label="Your reports">
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="absolute inset-0 bg-black/30"
      />
      <aside className="absolute right-0 top-0 flex h-full w-full max-w-lg flex-col bg-surface-default shadow-2xl">
        <header className="flex items-start justify-between border-b border-border px-4 py-3">
          <div className="min-w-0 flex-1">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
              <FileText size={16} />
              {openReport ? "Report contents" : "Your reports"}
            </h3>
            <p className="text-xs text-text-muted">
              {openReport
                ? "Rename it, remove items, or save it as PowerPoint / PDF / email."
                : "Build up a report over multiple questions, then save it as PowerPoint / PDF / email."}
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

        {openReport ? (
          <ReportDetailView
            report={openReport}
            onBack={() => setOpenReportId(null)}
            onRename={onRenameReport}
            onUnpin={onUnpin}
            onClear={onClear}
          />
        ) : (
          <div className="flex-1 overflow-y-auto">
            <ReportsListView
              reports={reports}
              activeReportId={activeReportId}
              onOpen={setOpenReportId}
              onSetActive={onSetActive}
              onDelete={onDeleteReport}
              onCreate={(title) => onCreateReport({ title, makeActive: true })}
            />
          </div>
        )}
      </aside>
    </div>
  );
}
