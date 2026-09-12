// Collapsed-by-default data sources pill. Users who want to know where the
// numbers came from can expand it; everyone else keeps a clean answer.
// The heavier "What Middleby needs to send" production-readiness ask now
// lives only in the Insight Session toolbar, where stakeholders looking at
// data readiness naturally end up.

import { useState } from "react";
import { ChevronDown, ChevronUp, Database } from "lucide-react";
import BlockShell from "../BlockShell";

const SYSTEM_ICON_COLORS = {
  GA4: "text-orange-500",
  LogRocket: "text-pink-500",
  HubSpot: "text-orange-600",
  Salesforce: "text-sky-600",
  "Adobe Commerce": "text-red-500",
  Akeneo: "text-emerald-600",
};

function SourceRow({ source }) {
  const color = SYSTEM_ICON_COLORS[source.system] || "text-text-muted";
  return (
    <li className="flex items-start gap-2 text-xs">
      <span className={`mt-0.5 h-2 w-2 shrink-0 rounded-full bg-current ${color}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <span className="font-medium text-text-primary">{source.system}</span>
        <span className="text-text-secondary"> • {source.scope}</span>
        {source.isReal ? (
          <span className="ml-2 rounded-full bg-status-success/10 px-1.5 py-0.5 text-[10px] font-medium text-status-success">
            Real data
          </span>
        ) : (
          <span
            className="ml-2 rounded-full bg-status-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-status-warning"
            title="This uses example numbers to show the shape of the answer. Middleby data would replace this."
          >
            Example data
          </span>
        )}
        {source.note ? (
          <p className="mt-0.5 text-[11px] text-text-muted">{source.note}</p>
        ) : null}
      </div>
    </li>
  );
}

export default function CitationBlock({ block }) {
  const [open, setOpen] = useState(false);
  const sources = block.sources || [];
  if (!sources.length) return null;

  return (
    <BlockShell block={block} bare>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-default px-2.5 py-1 text-[11px] font-medium text-text-secondary transition hover:border-primary/40 hover:text-primary"
        title="See the systems and windows that fed this answer"
      >
        <Database size={11} />
        Where this came from ({sources.length})
        {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
      </button>
      {open ? (
        <ul className="mt-2 space-y-1.5 rounded-xl border border-border bg-surface-secondary/40 p-3">
          {sources.map((s, i) => (
            <SourceRow key={`${s.system}-${i}`} source={s} />
          ))}
        </ul>
      ) : null}
    </BlockShell>
  );
}
