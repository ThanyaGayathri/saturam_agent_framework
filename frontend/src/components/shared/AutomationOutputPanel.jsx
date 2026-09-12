import { useState } from "react";
import {
  Download,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  Loader2,
  Mail,
  MessageSquare,
  ClipboardList,
} from "lucide-react";
import { downloadBlob } from "../../domain/answer-blocks/export-service";

/** Icon dispatcher for the different channel kinds (Slack / email / tasks). */
function ChannelIcon({ kind, className = "h-3.5 w-3.5" }) {
  if (kind === "slack") return <MessageSquare className={className} aria-hidden />;
  if (kind === "email") return <Mail className={className} aria-hidden />;
  if (kind === "hubspot-tasks") return <ClipboardList className={className} aria-hidden />;
  return <FileText className={className} aria-hidden />;
}

/**
 * Shared output surface for automation runs.
 *
 * Single-view layout (no tabs). Every channel and every report renders
 * in one stacked column so the viewer can see, at a glance:
 *   - the Slack post that went out
 *   - the email that was sent
 *   - the HubSpot tasks that were created
 *   - the reports that were produced (with PDF/CSV clearly labeled)
 *
 * Two modes:
 *   draft     - BEFORE approval. Section chips read "Draft, would post/send/
 *               create on approval". Report buttons say "Preview".
 *   delivered - AFTER approval. Section chips read "Posted to X at time" etc.
 *               Report buttons say "Open" (PDF/HTML in new tab) or "Download"
 *               (CSV via Blob).
 */
export default function AutomationOutputPanel({
  outputs,
  runLabel,
  mode = "delivered",
  deliveredAt = null,
  hideChannels = false,
}) {
  const hasChannels = outputs?.channels?.length > 0;
  const hasReports = outputs?.reports?.length > 0;
  if (!hasChannels && !hasReports) return null;

  const isDraft = mode === "draft";
  const channels = outputs?.channels ?? [];
  const reports = outputs?.reports ?? [];
  const showChannels = !hideChannels;

  return (
    <section
      className="rounded-xl border border-border bg-surface-default p-5 shadow-sm"
      aria-label={isDraft ? "Draft automation output" : "Automation output"}
    >
      <header className="mb-5">
        <h3 className="text-sm font-semibold text-text-primary">
          {isDraft ? "What will be sent when you approve" : "What this run produced"}
        </h3>
        <p className="mt-0.5 text-xs text-text-muted">
          {isDraft
            ? "Prototype simulation. Preview the report(s) below to see the exact content. Nothing is sent until you approve."
            : "Prototype simulation. Reports below are generated on your machine. No real messages are sent from this demo."}
        </p>
      </header>

      {hideChannels && hasChannels && !isDraft ? (
        <div className="mb-5 rounded-lg border border-border bg-surface-secondary/40 px-4 py-3 text-xs text-text-secondary">
          <span className="font-medium text-text-primary">{channels.length} notifications</span>{" "}
          were sent to stakeholders as part of this run (Slack, email, and system tasks). Full copy
          is included in the audit report below.
        </div>
      ) : null}

      {/* In draft mode, show a compact one-line-per-channel summary instead of
          full inline Slack / email / HubSpot renders. The Reports section
          below still lets users preview the actual PDF/CSV artifacts. */}
      {isDraft && showChannels && hasChannels ? (
        <div className="mb-5 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Ready to send on approval
          </p>
          <ul className="space-y-2">
            {channels.map((ch, i) => (
              <li
                key={ch.title + i}
                className="flex items-center gap-3 rounded-lg border border-border bg-surface-secondary/40 px-3 py-2 text-sm"
              >
                <ChannelIcon kind={ch.kind} />
                <span className="min-w-0 flex-1 text-text-primary">
                  {ch.kind === "slack"
                    ? `Slack post to ${ch.message.channel}`
                    : ch.kind === "email"
                    ? `Email to ${ch.message.to}`
                    : ch.kind === "hubspot-tasks"
                    ? `${ch.rows?.length ?? 0} tasks in ${ch.title.replace(/^[^·]+·\s*/, "")}`
                    : ch.title}
                </span>
                <span className="shrink-0 rounded-full bg-[#fffaeb] px-2 py-0.5 text-[10px] font-semibold text-[#b54708]">
                  Draft
                </span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-text-muted">
            Preview the report(s) below to see exactly what stakeholders will receive. Nothing is
            sent until you approve.
          </p>
        </div>
      ) : null}

      <div className="space-y-6">
        {showChannels && !isDraft
          ? channels.map((ch, i) => (
              <ChannelSection
                key={ch.title + i}
                channel={ch}
                mode={mode}
                reports={reports}
                deliveredAt={deliveredAt}
              />
            ))
          : null}

        {hasReports ? (
          <ReportsSection reports={reports} mode={mode} runLabel={runLabel} />
        ) : null}
      </div>
    </section>
  );
}

/** Format a Date as e.g. "Jul 22, 2026 · 3:14 PM". */
function formatDeliveredAt(d) {
  if (!(d instanceof Date) || isNaN(d.getTime())) return "";
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${date} · ${time}`;
}

// ==========================================================================
// Section helpers
// ==========================================================================

function SectionHeader({ icon: Icon, title, statusText, statusTone = "success" }) {
  const toneClass =
    statusTone === "warn"
      ? "bg-[#fffaeb] text-[#b54708]"
      : statusTone === "info"
      ? "bg-primary-light text-primary dark:bg-primary/15"
      : "bg-[#ecfdf3] text-[#067647]";
  const dotClass =
    statusTone === "warn"
      ? "bg-[#b54708]"
      : statusTone === "info"
      ? "bg-primary"
      : "bg-[#067647]";
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Icon className="h-4 w-4" aria-hidden />
        </div>
        <h4 className="text-sm font-semibold text-text-primary">{title}</h4>
      </div>
      <span
        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${toneClass}`}
      >
        <span className={`inline-block h-1.5 w-1.5 rounded-full ${dotClass}`} />
        {statusText}
      </span>
    </div>
  );
}

// ==========================================================================
// Channel section (Slack / Email / HubSpot tasks)
// ==========================================================================

function ChannelSection({ channel, mode, reports, deliveredAt }) {
  const isDraft = mode === "draft";
  const msg = channel.message;
  const deliveryLabel = formatDeliveredAt(deliveredAt);

  let title = channel.title;
  let statusText = "";
  let icon = FileText;
  let body = null;

  if (channel.kind === "slack") {
    icon = MessageSquare;
    title = `Slack post to ${msg.channel}`;
    statusText = isDraft
      ? `Draft. Would post when you approve.`
      : deliveryLabel
      ? `Posted at ${deliveryLabel}`
      : `Posted`;
    body = <SlackPreview msg={msg} deliveredAt={deliveredAt} isDraft={isDraft} />;
  } else if (channel.kind === "email") {
    icon = Mail;
    title = `Email to ${msg.to}`;
    statusText = isDraft
      ? `Draft. Would send when you approve.`
      : deliveryLabel
      ? `Sent at ${deliveryLabel}`
      : `Sent`;
    body = (
      <EmailPreview msg={msg} mode={mode} reports={reports} deliveredAt={deliveredAt} />
    );
  } else if (channel.kind === "hubspot-tasks") {
    icon = ClipboardList;
    const n = channel.rows?.length ?? 0;
    title = channel.title;
    statusText = isDraft
      ? `Draft. ${n} tasks would be created on approval.`
      : deliveryLabel
      ? `${n} tasks created at ${deliveryLabel}`
      : `${n} tasks created`;
    body = <HubspotTasksPreview rows={channel.rows} />;
  }

  return (
    <section className="space-y-3">
      <SectionHeader
        icon={icon}
        title={title}
        statusText={statusText}
        statusTone={isDraft ? "warn" : "success"}
      />
      {body}
    </section>
  );
}

function SlackPreview({ msg, deliveredAt, isDraft }) {
  const shownTimestamp = isDraft
    ? "Draft"
    : deliveredAt instanceof Date && !isNaN(deliveredAt.getTime())
    ? deliveredAt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : msg.timestamp;
  return (
    <div className="rounded-lg border border-border bg-surface-secondary/30 p-4">
      <div className="flex items-center gap-2 text-xs text-text-muted">
        <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-[#4A154B] text-[10px] font-bold text-white">
          #
        </span>
        <span className="font-medium text-text-primary">{msg.channel}</span>
      </div>
      <div className="mt-3 flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-primary/15 text-xs font-semibold text-primary">
          EG
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold text-text-primary">{msg.author}</span>
            <span className="text-xs text-text-muted">{shownTimestamp}</span>
          </div>
          <pre className="mt-1 whitespace-pre-wrap font-sans text-sm text-text-primary">
            {msg.body}
          </pre>
        </div>
      </div>
    </div>
  );
}

function EmailPreview({ msg, mode, reports, deliveredAt }) {
  const attachmentReport = findReportByFilename(reports, msg.attachment);
  const isDraft = mode === "draft";
  const sentLabel = isDraft
    ? "Not yet sent (draft)"
    : deliveredAt instanceof Date && !isNaN(deliveredAt.getTime())
    ? formatDeliveredAt(deliveredAt)
    : msg.sentAt;
  return (
    <div className="rounded-lg border border-border bg-surface-secondary/30">
      <div className="border-b border-border px-4 py-3 text-xs">
        <div className="grid grid-cols-[60px,1fr] gap-y-1 text-text-secondary">
          <span className="font-medium text-text-muted">From</span>
          <span className="text-text-primary">{msg.from}</span>
          <span className="font-medium text-text-muted">To</span>
          <span className="text-text-primary">{msg.to}</span>
          {msg.cc ? (
            <>
              <span className="font-medium text-text-muted">Cc</span>
              <span className="text-text-primary">{msg.cc}</span>
            </>
          ) : null}
          <span className="font-medium text-text-muted">Sent</span>
          <span className="text-text-primary">{sentLabel}</span>
        </div>
        <p className="mt-2 text-sm font-semibold text-text-primary">{msg.subject}</p>
      </div>
      <div className="px-4 py-4">
        <pre className="whitespace-pre-wrap font-sans text-sm text-text-primary">
          {msg.body}
        </pre>
        {msg.attachment ? (
          <EmailAttachmentChip
            filename={msg.attachment}
            report={attachmentReport}
            draftMode={mode === "draft"}
          />
        ) : null}
      </div>
    </div>
  );
}

function EmailAttachmentChip({ filename, report, draftMode }) {
  const [busy, setBusy] = useState(false);
  const clickable = Boolean(report) && !draftMode;
  const format = formatLabelForFilename(filename);
  const Icon = format === "CSV" ? FileSpreadsheet : FileText;

  const handle = async () => {
    if (!clickable || busy) return;
    setBusy(true);
    try {
      await openReport(report);
    } finally {
      setBusy(false);
    }
  };
  const title = draftMode
    ? "Approve the workflow to enable this attachment"
    : clickable
    ? `Opens ${filename}`
    : `${filename} (sample attachment)`;
  return (
    <button
      type="button"
      onClick={handle}
      disabled={!clickable}
      title={title}
      className={`mt-4 inline-flex items-center gap-2 rounded-md border px-3 py-2 text-xs transition ${
        clickable
          ? "border-border bg-surface-default text-text-primary hover:bg-surface-secondary/60 cursor-pointer"
          : "border-border bg-surface-default text-text-muted cursor-not-allowed opacity-90"
      }`}
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      ) : (
        <Icon className="h-3.5 w-3.5 text-text-muted" aria-hidden />
      )}
      <span className="font-medium">{filename}</span>
      <span className="text-text-muted">{format}</span>
      {clickable ? <ExternalLink className="h-3 w-3 text-text-muted" aria-hidden /> : null}
    </button>
  );
}

function HubspotTasksPreview({ rows }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="border-b border-border bg-surface-secondary/50 px-4 py-2 text-xs text-text-secondary">
        <span className="font-medium text-text-primary">{rows.length}</span> tasks staged
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-secondary/30 text-left text-xs uppercase tracking-wide text-text-muted">
            <tr>
              <th className="px-4 py-2 font-medium">Task</th>
              <th className="px-4 py-2 font-medium">Assignee</th>
              <th className="px-4 py-2 font-medium">Due</th>
              <th className="px-4 py-2 font-medium">Priority</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="px-4 py-2 text-text-primary">{r.taskName}</td>
                <td className="px-4 py-2 text-text-secondary">{r.assignee}</td>
                <td className="px-4 py-2 text-text-secondary">{r.dueDate}</td>
                <td className="px-4 py-2">
                  <span
                    className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                      r.priority === "High"
                        ? "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-100"
                        : "bg-surface-secondary text-text-secondary"
                    }`}
                  >
                    {r.priority}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ==========================================================================
// Reports section
// ==========================================================================

function ReportsSection({ reports, mode, runLabel }) {
  const isDraft = mode === "draft";
  const statusText = isDraft
    ? `${reports.length} reports drafted. Preview any file below.`
    : `${reports.length} reports produced`;
  return (
    <section className="space-y-3">
      <SectionHeader
        icon={FileText}
        title="Reports produced"
        statusText={statusText}
        statusTone="info"
      />
      <ul className="space-y-2">
        {reports.map((report) => (
          <li key={report.id}>
            <ReportRow report={report} runLabel={runLabel} mode={mode} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function ReportRow({ report, runLabel, mode }) {
  const [busy, setBusy] = useState(false);
  const isDownload = report.format === "csv";
  const isDraft = mode === "draft";
  const actionLabel = isDraft ? "Preview" : isDownload ? "Download" : "Open";
  const Icon = isDownload ? FileSpreadsheet : FileText;
  const formatLabel = report.format.toUpperCase();
  const formatHint = isDownload
    ? "Opens in Excel, Numbers, or Google Sheets"
    : report.format === "pdf"
    ? "Opens in a new browser tab"
    : "Opens in a new tab";

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await openReport(report);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-surface-secondary/30 p-4">
      <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <Icon className="h-5 w-5" aria-hidden />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <p className="text-sm font-semibold text-text-primary">{report.title}</p>
          <span
            className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-bold tracking-wide ${
              isDownload
                ? "bg-[#e6f4ea] text-[#0f7b3e]"
                : "bg-[#fbe9e9] text-[#b42318]"
            }`}
          >
            {formatLabel}
          </span>
        </div>
        {report.description ? (
          <p className="mt-0.5 text-xs text-text-secondary">{report.description}</p>
        ) : null}
        <p className="mt-1 text-[11px] text-text-muted">
          <span className="font-medium">{report.filename}</span>
          <span className="mx-1.5">·</span>
          <span>{formatHint}</span>
        </p>
      </div>
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        aria-label={`${actionLabel} ${report.title}${runLabel ? ` from ${runLabel}` : ""}`}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-primary-hover disabled:opacity-60"
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : isDownload ? (
          <Download className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
        )}
        {actionLabel}
      </button>
    </div>
  );
}

// ==========================================================================
// Shared file-opening helpers
// ==========================================================================

async function openReport(report) {
  const artifact = await report.build();
  if (report.format === "csv" && artifact instanceof Blob) {
    downloadBlob(artifact, report.filename);
  } else if (report.format === "pdf" && artifact instanceof Blob) {
    const url = URL.createObjectURL(artifact);
    window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } else if (report.format === "html" && artifact && typeof artifact.html === "string") {
    const w = window.open("", "_blank", "noopener,noreferrer");
    if (w) {
      w.document.open();
      w.document.write(artifact.html);
      w.document.close();
    }
  }
}

function formatLabelForFilename(filename) {
  if (!filename) return "FILE";
  const m = /\.([a-z0-9]+)$/i.exec(filename);
  return m ? m[1].toUpperCase() : "FILE";
}

function findReportByFilename(reports, filename) {
  if (!filename || !reports?.length) return null;
  return reports.find((r) => r.filename === filename) ?? null;
}
