// "How this dataset was built" footer  -  replaces the older "data honesty"
// component that was heavy on cross-source-window caveats. Now it just tells
// the audience how the numbers on this page were generated, so they know
// what's real and what's synthetic without dwelling on export dates.

import { Sparkles } from "lucide-react";
import { buildDataHonestyBadges, isMiddlebyBlendedSession } from "../../domain/middleby-data-honesty";

export default function MiddlebyDataHonestyFooter({ spec, rows, entry }) {
  const badges = buildDataHonestyBadges(spec, rows, entry);
  const blended = isMiddlebyBlendedSession(spec, rows);

  if (!badges.length && !blended) return null;

  return (
    <footer className="mt-6 rounded-xl border border-border bg-surface-secondary/60 px-4 py-4">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
        <Sparkles className="h-3.5 w-3.5" aria-hidden />
        How this dataset was built
      </div>
      <p className="mt-2 text-xs text-text-secondary">
        The numbers on this page use a mix of Middleby's real analytics exports and synthetic values
        generated to match the shape and distribution of production data. Where a metric is fully synthetic,
        the generation rule is included below so the audience can see how it was produced.
      </p>
      {badges.length ? (
        <ul className="mt-3 flex flex-wrap gap-2">
          {badges.map((b) => (
            <li
              key={b.id}
              title={b.detail || undefined}
              className="rounded-full border border-border bg-surface-default px-2.5 py-1 text-xs font-medium text-text-primary"
            >
              {b.label}
              {b.detail ? <span className="ml-1 font-normal text-text-muted">· {b.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {blended ? (
        <div className="mt-3 rounded-lg border border-primary/20 bg-primary-light/40 px-3 py-3 dark:bg-primary/10">
          <p className="text-xs font-semibold text-primary">Blended sources</p>
          <p className="mt-1 text-sm text-text-secondary">
            This view stitches Middleby's own GA4, LogRocket, HubSpot, and (post-sync) Salesforce data.
            Where real records don't yet join across systems, the demo uses matched synthetic examples with
            realistic distributions so the story holds together end to end.
          </p>
        </div>
      ) : null}
    </footer>
  );
}
