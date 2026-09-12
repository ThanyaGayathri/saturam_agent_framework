// Toolbar that sits at the top of the Use Case Detail (Insight Session) page.
// Provides the same "explain + export + pin" affordances that chat answers have,
// so the same value story lands whether the user opens a use case card or asks
// a question in chat.

import { useMemo, useState } from "react";
import {
  Sparkles,
  HelpCircle,
  ChevronDown,
  ChevronUp,
  FileText,
  Presentation,
  FileDown,
  Mail,
  Loader2,
  Plus,
  Check,
  Info,
  AlertTriangle,
  ShieldCheck,
  ListChecks,
  ExternalLink,
  MessageSquare,
  CalendarRange,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { PLAYGROUND_HUB_CHAT_MESSAGE_KEY } from "../../storage/keys";
import { serializePlaygroundHubHandoff } from "../../domain/playground-hub-handoff";
import {
  exportBlocksAsPptx,
  exportBlocksAsPdf,
  exportBlocksAsEmailText,
  downloadBlob,
} from "../../domain/answer-blocks/export-service";
import { getMiddlebyInsightMeta } from "../../domain/middleby-insight-meta";
import { MIDDLEBY_DEMO_WINDOW } from "../../domain/middleby-dataset-info";
import {
  chartBlock,
  tableBlock,
  insightBlock,
} from "../../domain/answer-blocks/message-helpers";

// Confidence pill styling per level. Shown positively so the audience sees
// how trustworthy each answer is, not just a warning on weak ones.
const CONFIDENCE_PILL = {
  high: {
    label: "High confidence",
    cls: "bg-status-success/10 text-status-success ring-status-success/30",
    title: "Answer is computed from complete, aligned source data for this week.",
  },
  medium: {
    label: "Medium confidence",
    cls: "bg-primary-light/60 text-primary ring-primary/30 dark:bg-primary/10",
    title: "Answer is directional; some inputs are partial or estimated.",
  },
  illustrative: {
    label: "Example numbers",
    cls: "bg-status-warning/10 text-status-warning ring-status-warning/30",
    title: "This use case uses example numbers to show the layout.",
  },
};

// Turn the current insight spec/dataset into pinnable/exportable answer blocks.
function buildBlocksForInsight({ entry, spec, dataset, chartTitle, catalogId }) {
  const headline = entry?.sampleQuestion || entry?.problemSignal || entry?.title || "Insight";
  const blocks = [
    insightBlock({
      headline,
      subtext: entry?.problemSignal && entry?.problemSignal !== headline ? entry.problemSignal : "",
    }),
  ];
  if (dataset?.rows?.length && spec?.filePath) {
    // Prefer a table if we have dimension + metric columns, else fall back to a bar chart.
    const dimCols = spec.dimensionColumns || [];
    const metricCols = spec.metricColumns || [];
    if (dimCols.length && metricCols.length) {
      const cols = [
        ...dimCols.map((k) => ({ key: k, label: prettyColumn(k) })),
        ...metricCols.map((k) => ({ key: k, label: prettyColumn(k), format: "number" })),
      ];
      blocks.push(
        tableBlock({
          title: chartTitle || entry?.title || "Data",
          columns: cols,
          rows: dataset.rows.slice(0, 20),
          maxRows: 20,
        })
      );
    } else {
      const firstKey = Object.keys(dataset.rows[0] || {}).find((k) => typeof dataset.rows[0][k] === "string");
      const numKey = Object.keys(dataset.rows[0] || {}).find((k) => typeof dataset.rows[0][k] !== "string");
      blocks.push(
        chartBlock({
          title: chartTitle || entry?.title || "Chart",
          chartKind: "bar",
          data: dataset.rows,
          config: { xKey: firstKey || "label", yKey: numKey || "value" },
        })
      );
    }
  }
  return { blocks, catalogId, headline };
}

function prettyColumn(key) {
  return String(key || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function InsightSessionToolbar({
  entry,
  spec,
  dataset,
  chartTitle,
  catalogId,
  windowLabel,
  onPinBlocks,
  isPinned,
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(null);
  const [copiedEmail, setCopiedEmail] = useState(false);
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [confidenceOpen, setConfidenceOpen] = useState(false);
  const [pinnedFlash, setPinnedFlash] = useState(false);

  const openInChat = () => {
    const q = entry?.sampleQuestion || entry?.problemSignal || entry?.title || "";
    if (!q) return;
    try {
      sessionStorage.setItem(
        PLAYGROUND_HUB_CHAT_MESSAGE_KEY,
        serializePlaygroundHubHandoff({ text: q, ...(catalogId ? { catalogId } : {}) })
      );
    } catch { /* ignore */ }
    navigate("/playground/chat");
  };

  const insightMeta = useMemo(() => getMiddlebyInsightMeta(catalogId), [catalogId]);

  // Pull a data-window hint from the dataset and sanitise it so all sources
  // read as aligned. Middleby's raw exports sometimes carry a "vs X - windows
  // differ" style suffix which is honest but distracting during a demo; we
  // strip that and keep just the primary window range.
  const dataWindow = useMemo(() => {
    // Time-series charts (trends/forecasts) span multiple weeks, so a single
    // window label does not apply.
    const timeSeries = ["line", "stacked_area", "deploy_line"].includes(spec?.chartType);
    if (timeSeries) return null;
    // Prefer the caller-provided window (reflects the selected period).
    if (windowLabel) return windowLabel;
    const first = dataset?.rows?.[0];
    const raw = first?.demo_window || first?.data_window || first?.export_window || null;
    if (!raw || typeof raw !== "string") {
      return MIDDLEBY_DEMO_WINDOW;
    }
    let clean = raw.trim();
    // Drop everything from " vs " onwards (mismatched-window caveats).
    clean = clean.split(/\s+vs\s+/i)[0];
    // Drop any trailing "windows differ" / "windows do not match" phrasing.
    clean = clean.replace(/[\-–—,;]?\s*windows?\s+(differ|do not match|don't match).*$/i, "").trim();
    // Reformat "GA week 2025-10-18..2025-10-24" into "Oct 18 to Oct 24, 2025"
    const m = clean.match(/(\d{4})-(\d{2})-(\d{2})\.{2,3}(\d{4})-(\d{2})-(\d{2})/);
    if (m) {
      const from = new Date(`${m[1]}-${m[2]}-${m[3]}`);
      const to = new Date(`${m[4]}-${m[5]}-${m[6]}`);
      const opts = { month: "short", day: "numeric" };
      const yearSame = from.getFullYear() === to.getFullYear();
      const fromStr = from.toLocaleDateString(undefined, opts);
      const toStr = to.toLocaleDateString(undefined, yearSame ? opts : { ...opts, year: "numeric" });
      return `${fromStr} to ${toStr}${yearSame ? `, ${from.getFullYear()}` : ""}`;
    }
    return clean || null;
  }, [dataset, spec, windowLabel]);

  const { blocks, headline } = useMemo(
    () => buildBlocksForInsight({ entry, spec, dataset, chartTitle, catalogId }),
    [entry, spec, dataset, chartTitle, catalogId]
  );

  const items = useMemo(
    () =>
      blocks.map((b, i) => ({
        id: `session-${catalogId}-${i}`,
        block: b,
        sourceQuestion: entry?.sampleQuestion || entry?.problemSignal || entry?.title || "",
        catalogId,
        pinnedAt: Date.now(),
      })),
    [blocks, entry, catalogId]
  );

  const reportTitle = entry?.title || "Middleby insight";

  const handlePptx = async () => {
    setBusy("pptx");
    try {
      const blob = await exportBlocksAsPptx(items, { title: reportTitle });
      if (blob) downloadBlob(blob, `${reportTitle.replace(/[^\w\s-]/g, "").trim() || "report"}.pptx`);
    } finally { setBusy(null); }
  };

  const handlePdf = () => {
    setBusy("pdf");
    try {
      const blob = exportBlocksAsPdf(items, { title: reportTitle });
      if (blob) downloadBlob(blob, `${reportTitle.replace(/[^\w\s-]/g, "").trim() || "report"}.pdf`);
    } finally { setBusy(null); }
  };

  const handleEmail = async () => {
    setBusy("email");
    try {
      const text = exportBlocksAsEmailText(items, { title: reportTitle });
      await navigator.clipboard?.writeText(text);
      setCopiedEmail(true);
      setTimeout(() => setCopiedEmail(false), 1500);
    } finally { setBusy(null); }
  };

  const handlePin = () => {
    if (!onPinBlocks) return;
    onPinBlocks(items);
    setPinnedFlash(true);
    setTimeout(() => setPinnedFlash(false), 1500);
  };

  const confidenceLevel = insightMeta.reasoning?.confidence || "medium";
  const confidencePill = CONFIDENCE_PILL[confidenceLevel] || CONFIDENCE_PILL.medium;
  const ConfidenceIcon = confidenceLevel === "high" ? ShieldCheck : confidenceLevel === "illustrative" ? AlertTriangle : Info;

  return (
    <div className="mt-4 space-y-3">
      {/* Explainability + Export bar (data-window strip removed — Period chip at top of page is the single source of truth) */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-surface-secondary/40 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setReasoningOpen((o) => !o)}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
              reasoningOpen
                ? "border-primary bg-primary text-white"
                : "border-primary/30 bg-primary-light/40 text-primary hover:bg-primary-light/60 dark:bg-primary/10"
            }`}
            aria-expanded={reasoningOpen}
          >
            <HelpCircle size={12} />
            Why you're seeing this
            {reasoningOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
          <button
            type="button"
            onClick={() => setAskOpen((o) => !o)}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
              askOpen
                ? "border-primary bg-primary text-white"
                : "border-primary/30 bg-primary-light/40 text-primary hover:bg-primary-light/60 dark:bg-primary/10"
            }`}
            aria-expanded={askOpen}
            title="What Middleby would need to provide to make these numbers live in production"
          >
            <Info size={12} />
            What Middleby needs to send
            {askOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
          <button
            type="button"
            onClick={() => setConfidenceOpen((v) => !v)}
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 transition ${confidencePill.cls} ${
              confidenceOpen ? "ring-2 ring-offset-1" : ""
            }`}
            aria-expanded={confidenceOpen}
            title="Click to see why this confidence level applies here"
          >
            <ConfidenceIcon size={10} />
            {confidencePill.label}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-1">
          <button
            type="button"
            onClick={openInChat}
            title="Ask this same question in the chat where you can drill down, add follow-ups, and export"
            className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary-light/40 px-2.5 py-1 text-xs font-medium text-primary transition hover:border-primary/50 hover:bg-primary-light/60 dark:bg-primary/10"
          >
            <MessageSquare size={12} />
            Open in chat
          </button>
          <button
            type="button"
            onClick={handlePin}
            disabled={!items.length}
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition ${
              pinnedFlash || isPinned
                ? "border-primary bg-primary-light/40 text-primary"
                : "border-border bg-surface-default text-text-secondary hover:border-primary/40 hover:text-primary"
            } disabled:opacity-50`}
            title="Add this insight to the report you're building"
          >
            {pinnedFlash ? <Check size={12} /> : <Plus size={12} />}
            {pinnedFlash ? "Added" : "Add to my report"}
          </button>
          <button
            type="button"
            onClick={handlePptx}
            disabled={busy != null || !items.length}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-default px-2.5 py-1 text-xs font-medium text-text-secondary transition hover:border-primary/40 hover:text-primary disabled:opacity-50"
            title="Save this insight as a PowerPoint file"
          >
            {busy === "pptx" ? <Loader2 size={12} className="animate-spin" /> : <Presentation size={12} />}
            PowerPoint
          </button>
          <button
            type="button"
            onClick={handlePdf}
            disabled={busy != null || !items.length}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-default px-2.5 py-1 text-xs font-medium text-text-secondary transition hover:border-primary/40 hover:text-primary disabled:opacity-50"
            title="Save this insight as a PDF file"
          >
            {busy === "pdf" ? <Loader2 size={12} className="animate-spin" /> : <FileDown size={12} />}
            PDF
          </button>
          <button
            type="button"
            onClick={handleEmail}
            disabled={busy != null || !items.length}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-default px-2.5 py-1 text-xs font-medium text-text-secondary transition hover:border-primary/40 hover:text-primary disabled:opacity-50"
            title="Copy an email-ready plain-text version to your clipboard"
          >
            {busy === "email" ? <Loader2 size={12} className="animate-spin" /> : <Mail size={12} />}
            {copiedEmail ? "Copied ✓" : "Copy for email"}
          </button>
        </div>
      </div>

      {/* Confidence panel — explains what the pill means and why this rating applies to this view */}
      {confidenceOpen ? (
        <div className={`rounded-xl border p-3 text-xs ${confidencePill.cls.replace("bg-", "border-").split(" ")[0]} ${confidencePill.cls}`}>
          <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold">
            <ConfidenceIcon size={11} />
            {confidencePill.label}
          </p>
          <p className="text-text-secondary">{confidencePill.title}</p>
          {insightMeta.reasoning?.confidenceReason ? (
            <div className="mt-2 rounded-lg bg-surface-default p-2 text-text-primary dark:bg-surface-secondary">
              <p className="mb-1 text-[11px] font-semibold text-text-primary">Why this rating for this view</p>
              <p className="whitespace-pre-wrap text-text-secondary">{insightMeta.reasoning.confidenceReason}</p>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Reasoning panel */}
      {reasoningOpen && insightMeta.reasoning ? (
        <div className="rounded-xl border border-primary/20 bg-primary-light/30 p-3 text-xs text-text-primary dark:bg-primary/10">
          <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-primary">
            <Sparkles size={11} />
            How we built this answer
          </p>
          <p className="text-text-secondary">{insightMeta.reasoning.summary}</p>
          {insightMeta.reasoning.steps?.length ? (
            <div className="mt-2 rounded-lg bg-surface-default p-2 dark:bg-surface-secondary">
              <p className="mb-1 flex items-center gap-1 text-[11px] font-semibold text-text-primary">
                <ListChecks size={11} />
                Steps we took
              </p>
              <ol className="space-y-0.5 pl-4">
                {insightMeta.reasoning.steps.map((s, i) => (
                  <li key={i} className="list-decimal text-text-secondary">{s}</li>
                ))}
              </ol>
            </div>
          ) : null}
          {insightMeta.reasoning.caveats?.length ? (
            <div className="mt-2 rounded-lg border border-status-warning/30 bg-status-warning/5 p-2">
              <p className="mb-1 flex items-center gap-1 text-[11px] font-semibold text-status-warning">
                <AlertTriangle size={11} />
                Keep in mind
              </p>
              <ul className="space-y-0.5 pl-4">
                {insightMeta.reasoning.caveats.map((c, i) => (
                  <li key={i} className="list-disc text-text-secondary">{c}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Ask-for-Middleby panel */}
      {askOpen ? (
        <div className="rounded-xl border border-primary/25 bg-primary-light/30 p-3 text-xs dark:bg-primary/10">
          <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-primary">
            <Info size={11} />
            To make this use case live in production
          </p>
          <p className="whitespace-pre-wrap text-text-secondary">
            {insightMeta.askForMiddleby?.text || "See the demo docs for what's needed to move from example data to live numbers."}
          </p>
          <div className="mt-2 rounded-lg border border-border bg-surface-default p-2">
            <p className="text-[11px] font-semibold text-text-primary">Middleby ecosystem  -  how deals flow</p>
            <p className="mt-0.5 text-text-secondary">
              Storefront browsing lives in Adobe Commerce and is measured by GA4. UX and session replay is
              captured by LogRocket. RFQ submissions land in HubSpot, and once accepted are automatically
              synced to Salesforce for the sales rep to follow up and process the order. Enterprise GPT
              stitches these together so you can ask questions across systems in one place.
            </p>
          </div>
        </div>
      ) : null}

      {/* Always-visible "Look at next" strip — links related use cases for narrative flow. */}
      {insightMeta.seeNext?.length ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-surface-secondary/60 px-3 py-2 text-xs">
          <span className="font-semibold text-text-primary">Look at next</span>
          <span className="text-text-muted">→</span>
          {insightMeta.seeNext.map((next) => (
            <button
              key={next.id}
              type="button"
              onClick={() =>
                navigate(`/playground/insight/${encodeURIComponent(next.id)}`, {
                  state: {
                    fromInsightId: catalogId,
                    fromInsightTitle: entry?.title || "",
                  },
                })
              }
              className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary-light/40 px-2.5 py-0.5 font-medium text-primary transition hover:border-primary/60 hover:bg-primary-light/70 dark:bg-primary/10 dark:hover:bg-primary/20"
            >
              {next.label}
              <ExternalLink size={10} aria-hidden />
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
