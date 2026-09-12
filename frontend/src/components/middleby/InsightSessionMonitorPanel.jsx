import { useMemo } from "react";
import { TrendingUp, ArrowUpRight, ArrowDownRight, Minus } from "lucide-react";
import { getMiddlebyTrackOverTime } from "../../domain/middleby-track-over-time";

/**
 * Track-over-time tab body.
 *
 * Rewritten to render use-case-SPECIFIC trend content (headline narrative,
 * weekly metric series, and per-week signals) rather than the shared
 * business monitor. Every tab on a Middleby use case detail page now stays
 * on the same topic in the same language.
 */
export default function InsightSessionMonitorPanel({ catalogId, useCaseTitle }) {
  const track = useMemo(() => getMiddlebyTrackOverTime(catalogId), [catalogId]);

  if (!track) {
    return (
      <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
        <p className="text-sm text-text-secondary">
          Trend view not yet configured for this use case.
        </p>
      </section>
    );
  }

  const points = Array.isArray(track.weeklyPoints) ? track.weeklyPoints : [];
  const first = points[0]?.value ?? 0;
  const last = points[points.length - 1]?.value ?? 0;
  const delta = typeof first === "number" && typeof last === "number" ? last - first : 0;
  const unit = track.metricUnit || "";

  const values = points.map((p) => Number(p.value)).filter((v) => !Number.isNaN(v));
  const minV = values.length ? Math.min(...values) : 0;
  const maxV = values.length ? Math.max(...values) : 1;
  const range = Math.max(0.0001, maxV - minV);

  return (
    <div className="space-y-6">
      <header className="max-w-2xl">
        <h2 className="text-lg font-semibold text-text-primary">{track.title || useCaseTitle || "Trend view"}</h2>
        <p className="mt-1 text-sm text-text-secondary">{track.question}</p>
      </header>

      {track.headline ? (
        <section className="rounded-xl border border-border bg-surface-secondary/50 px-4 py-4">
          <div className="flex items-start gap-3">
            <TrendingUp className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
            <p className="text-sm leading-relaxed text-text-primary">{track.headline}</p>
          </div>
        </section>
      ) : null}

      {points.length > 0 ? (
        <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
          <div className="flex flex-wrap items-baseline justify-between gap-2 mb-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                {track.metricLabel || "Weekly trend"}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums text-text-primary">
                {last}{unit}
                <span className={`ml-2 inline-flex items-center gap-0.5 text-sm font-medium ${
                  delta > 0 ? "text-status-success" : delta < 0 ? "text-status-danger" : "text-text-muted"
                }`}>
                  {delta > 0 ? <ArrowUpRight className="h-3.5 w-3.5" /> : delta < 0 ? <ArrowDownRight className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
                  {delta > 0 ? "+" : ""}{Math.round(delta * 10) / 10}{unit}
                </span>
              </p>
              <p className="text-xs text-text-muted">
                {points[0]?.week} → {points[points.length - 1]?.week} (12 weeks)
              </p>
            </div>
          </div>

          {/* Inline SVG sparkline chart */}
          <svg viewBox="0 0 400 120" className="h-32 w-full" preserveAspectRatio="none">
            <defs>
              <linearGradient id="trackFill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="#7f56d9" stopOpacity="0.28" />
                <stop offset="100%" stopColor="#7f56d9" stopOpacity="0" />
              </linearGradient>
            </defs>
            {(() => {
              const stepX = points.length > 1 ? 400 / (points.length - 1) : 0;
              const scaleY = (v) => 120 - ((v - minV) / range) * 100 - 10;
              const linePath = points
                .map((p, i) => `${i === 0 ? "M" : "L"} ${(i * stepX).toFixed(2)} ${scaleY(Number(p.value)).toFixed(2)}`)
                .join(" ");
              const areaPath = `${linePath} L 400 120 L 0 120 Z`;
              return (
                <>
                  <path d={areaPath} fill="url(#trackFill)" />
                  <path d={linePath} fill="none" stroke="#7f56d9" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  {points.map((p, i) => (
                    <circle
                      key={i}
                      cx={(i * stepX).toFixed(2)}
                      cy={scaleY(Number(p.value)).toFixed(2)}
                      r="2.5"
                      fill="#7f56d9"
                    />
                  ))}
                </>
              );
            })()}
          </svg>

          <div className="mt-3 grid grid-cols-6 gap-1 text-[10px] text-text-muted sm:grid-cols-12">
            {points.map((p, i) => (
              <span key={i} className="text-center tabular-nums">{p.value}{unit}</span>
            ))}
          </div>
        </section>
      ) : null}

      {Array.isArray(track.signals) && track.signals.length > 0 ? (
        <section className="rounded-xl border border-border bg-surface-secondary/40 px-4 py-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-3">
            What happened during the window
          </h3>
          <ul className="space-y-3">
            {track.signals.map((s, i) => (
              <li key={i} className="rounded-lg border border-border bg-surface-default px-3 py-2">
                <p className="text-sm font-semibold text-text-primary">{s.title}</p>
                {s.detail ? <p className="mt-0.5 text-xs text-text-secondary">{s.detail}</p> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
