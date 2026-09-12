import { useState } from "react";
import { ChevronDown, ChevronRight, Database } from "lucide-react";
import {
  MIDDLEBY_CROSS_SOURCE_EXPLANATION,
  MIDDLEBY_DATASET_SUMMARY,
  MIDDLEBY_ILLUSTRATIVE_WINDOWS_LINE,
  MIDDLEBY_REAL_EXPORT_WINDOWS_LINE,
  MIDDLEBY_SOURCE_WINDOWS,
} from "../../domain/middleby-dataset-info";

const KIND_STYLES = {
  "Real export": "bg-emerald-50 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-100",
  "Real export (anonymised)": "bg-emerald-50 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-100",
  "Real + blended estimates": "bg-sky-50 text-sky-900 dark:bg-sky-500/15 dark:text-sky-100",
  "Blended real slices": "bg-violet-50 text-violet-900 dark:bg-violet-500/15 dark:text-violet-100",
  "Demo sample": "bg-amber-50 text-amber-950 dark:bg-amber-500/15 dark:text-amber-100",
  "Catalog reference": "bg-surface-secondary text-text-secondary",
};

function AccordionSection({ id, title, teaser, expanded, onToggle, children }) {
  const panelId = `middleby-dataset-${id}`;

  return (
    <div className="border-t border-primary/15 first:border-t-0">
      <button
        type="button"
        id={`${panelId}-trigger`}
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={onToggle}
        className="flex w-full items-start gap-2 py-3 text-left transition hover:text-text-primary"
      >
        <span className="mt-0.5 shrink-0 text-text-muted" aria-hidden>
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-text-primary">{title}</span>
          {!expanded && teaser ? (
            <span className="mt-0.5 block text-xs text-text-muted">{teaser}</span>
          ) : null}
        </span>
      </button>
      {expanded ? (
        <div id={panelId} role="region" aria-labelledby={`${panelId}-trigger`} className="pb-4 pl-6">
          {children}
        </div>
      ) : null}
    </div>
  );
}

export default function MiddlebyAboutDatasetPanel() {
  const { headline, intro, footnote, stats } = MIDDLEBY_DATASET_SUMMARY;
  const crossSource = MIDDLEBY_CROSS_SOURCE_EXPLANATION;
  const [aboutOpen, setAboutOpen] = useState(false);
  const [periodsOpen, setPeriodsOpen] = useState(false);

  const periodsTeaser = "GA Oct 18-24 · HubSpot Sep 19 export · LogRocket Apr-Jun 2026";

  return (
    <section className="rounded-xl border border-primary/20 bg-primary-light/30 p-5 dark:bg-primary/10">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary">
          <Database className="h-5 w-5" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <AccordionSection
            id="about"
            title={headline}
            teaser="Separate demo windows per source; blended charts join by brand, not by date."
            expanded={aboutOpen}
            onToggle={() => setAboutOpen((v) => !v)}
          >
            <p className="text-sm text-text-secondary">{intro}</p>

            <div className="mt-3 space-y-1 text-sm text-text-secondary">
              <p>{MIDDLEBY_REAL_EXPORT_WINDOWS_LINE}</p>
              <p>{MIDDLEBY_ILLUSTRATIVE_WINDOWS_LINE}</p>
            </div>

            <div className="mt-4 rounded-lg border border-violet-200/80 bg-violet-50/60 px-3 py-3 dark:border-violet-500/25 dark:bg-violet-500/10">
              <p className="text-sm font-medium text-text-primary">{crossSource.lead}</p>
              <p className="mt-1.5 text-sm text-text-secondary">{crossSource.detail}</p>
              <p className="mt-1.5 text-sm text-text-secondary">{crossSource.join}</p>
            </div>

            <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {stats.map((s) => (
                <div key={s.label} className="rounded-lg border border-border/80 bg-surface-default px-3 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                    {s.label}
                  </dt>
                  <dd className="mt-0.5 text-sm font-semibold tabular-nums text-text-primary">{s.value}</dd>
                </div>
              ))}
            </dl>

            <p className="mt-3 text-xs text-text-muted">{footnote}</p>
          </AccordionSection>

          <AccordionSection
            id="periods"
            title="Sample periods by source"
            teaser={periodsTeaser}
            expanded={periodsOpen}
            onToggle={() => setPeriodsOpen((v) => !v)}
          >
            <div className="overflow-x-auto rounded-lg border border-border bg-surface-default">
              <table className="min-w-full text-left text-sm">
                <thead className="border-b border-border bg-surface-secondary text-xs uppercase tracking-wide text-text-muted">
                  <tr>
                    <th className="px-3 py-2 font-semibold">Source</th>
                    <th className="px-3 py-2 font-semibold">Demo window</th>
                    <th className="hidden px-3 py-2 font-semibold sm:table-cell">Used for</th>
                    <th className="px-3 py-2 font-semibold">Type</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {MIDDLEBY_SOURCE_WINDOWS.map((row) => (
                    <tr key={row.id}>
                      <td className="whitespace-nowrap px-3 py-2.5 font-medium text-text-primary">
                        {row.source}
                      </td>
                      <td className="px-3 py-2.5 text-text-secondary">{row.period}</td>
                      <td className="hidden max-w-xs px-3 py-2.5 text-text-secondary sm:table-cell">
                        {row.examples}
                      </td>
                      <td className="px-3 py-2.5">
                        <span
                          className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                            KIND_STYLES[row.kind] || "bg-surface-secondary text-text-secondary"
                          }`}
                        >
                          {row.kind}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="mt-3 space-y-1.5 text-xs text-text-secondary sm:hidden">
              {MIDDLEBY_SOURCE_WINDOWS.map((row) => (
                <li key={`${row.id}-mobile`}>
                  <span className="font-medium text-text-primary">{row.source}:</span> {row.examples}
                </li>
              ))}
            </ul>
          </AccordionSection>
        </div>
      </div>
    </section>
  );
}
