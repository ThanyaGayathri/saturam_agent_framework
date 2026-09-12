// Extended Portfolio Performance sections  -  mirrors what McFadyen's Power BI
// screenshots track so the demo lines up with what stakeholders expect to see:
// Traffic by channel, RFQ form funnel breakdown, Consultation vs self-serve
// mix, Category performance leaderboard.

import { ArrowUpRight, ArrowDownRight, Users, Filter, MessageSquare, ListOrdered, HelpCircle } from "lucide-react";
import {
  PORTFOLIO_TRAFFIC_BY_CHANNEL,
  PORTFOLIO_RFQ_FORM_FUNNEL,
  PORTFOLIO_CONSULTATION_MIX,
  PORTFOLIO_CATEGORY_LEADERBOARD,
  PORTFOLIO_EXTENDED_KPIS,
} from "../../domain/middleby-portfolio-extended-data";

const CHANNEL_COLORS = ["#6941C6", "#7F56D9", "#9E77ED", "#B692F6", "#D6BBFB"];

function fmt(n) {
  return new Intl.NumberFormat().format(n);
}

function pct(n, digits = 0) {
  return `${n.toFixed(digits)}%`;
}

function ChannelMixChart({ rows }) {
  const total = rows.reduce((s, r) => s + r.sessions, 0);
  return (
    <div className="space-y-2">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-surface-secondary">
        {rows.map((r, i) => (
          <div
            key={r.channel}
            title={`${r.channel}: ${pct((r.sessions / total) * 100, 1)}`}
            className="h-full"
            style={{
              width: `${(r.sessions / total) * 100}%`,
              backgroundColor: CHANNEL_COLORS[i % CHANNEL_COLORS.length],
            }}
          />
        ))}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
        {rows.map((r, i) => (
          <div key={r.channel} className="flex items-center gap-2 text-xs">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: CHANNEL_COLORS[i % CHANNEL_COLORS.length] }}
              aria-hidden
            />
            <span className="min-w-0 flex-1 truncate text-text-secondary">{r.channel}</span>
            <span className="tabular-nums font-medium text-text-primary">{pct(r.share)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TrendArrow({ delta }) {
  if (delta > 0) return <ArrowUpRight size={12} className="text-status-success" aria-hidden />;
  if (delta < 0) return <ArrowDownRight size={12} className="text-status-critical" aria-hidden />;
  return null;
}

export default function PortfolioExtendedSections() {
  const totalRfqs = PORTFOLIO_TRAFFIC_BY_CHANNEL.reduce((s, r) => s + r.rfqs, 0);
  const bestChannel = [...PORTFOLIO_TRAFFIC_BY_CHANNEL].sort((a, b) => b.conversion - a.conversion)[0];

  return (
    <>
      {/* Traffic by channel */}
      <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <Users className="h-4 w-4 text-primary" aria-hidden />
          <h2 className="text-sm font-semibold text-text-primary">Traffic by channel</h2>
        </div>
        <p className="mt-1 text-xs text-text-secondary">
          Where {fmt(PORTFOLIO_EXTENDED_KPIS.trafficTotal)} sessions came from this window.
          Referral traffic converts best ({pct(bestChannel.conversion, 2)}) despite being the smallest source.
        </p>
        <div className="mt-4">
          <ChannelMixChart rows={PORTFOLIO_TRAFFIC_BY_CHANNEL} />
        </div>
        <div className="mt-4 overflow-x-auto rounded-lg border border-border">
          <table className="min-w-full divide-y divide-border text-left text-sm">
            <thead className="bg-surface-secondary">
              <tr>
                <th className="px-3 py-2 font-semibold text-text-secondary">Channel</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Sessions</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Share</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">RFQs</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Conversion</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {PORTFOLIO_TRAFFIC_BY_CHANNEL.map((r) => (
                <tr key={r.channel}>
                  <td className="px-3 py-2 font-medium text-text-primary">{r.channel}</td>
                  <td className="px-3 py-2 tabular-nums text-text-secondary">{fmt(r.sessions)}</td>
                  <td className="px-3 py-2 tabular-nums text-text-secondary">{pct(r.share)}</td>
                  <td className="px-3 py-2 tabular-nums text-text-secondary">{fmt(r.rfqs)}</td>
                  <td className="px-3 py-2 tabular-nums text-text-secondary">{pct(r.conversion, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-text-muted">
          {fmt(totalRfqs)} RFQs across all channels in this window.
        </p>
      </section>

      {/* RFQ form funnel */}
      <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-primary" aria-hidden />
          <h2 className="text-sm font-semibold text-text-primary">RFQ form funnel</h2>
        </div>
        <p className="mt-1 text-xs text-text-secondary">
          Where visitors leave between opening the RFQ form and submitting it. Abandonment sits at {pct(PORTFOLIO_EXTENDED_KPIS.formAbandonRate, 1)}  -  high, and worth investigating in the UX view.
        </p>
        <div className="mt-4 space-y-2">
          {PORTFOLIO_RFQ_FORM_FUNNEL.map((step, i) => {
            const first = PORTFOLIO_RFQ_FORM_FUNNEL[0].value;
            const pctOfTop = (step.value / first) * 100;
            const deltaPct = ((step.value - step.prev) / step.prev) * 100;
            return (
              <div key={step.stage} className="flex items-center gap-3">
                <div className="w-44 shrink-0 text-xs text-text-secondary">{step.stage}</div>
                <div className="relative h-6 flex-1 rounded-full bg-surface-secondary">
                  <div
                    className="absolute inset-y-0 left-0 rounded-full"
                    style={{ width: `${Math.max(6, pctOfTop)}%`, backgroundColor: CHANNEL_COLORS[i % CHANNEL_COLORS.length] }}
                  />
                  <span className="absolute inset-y-0 left-3 flex items-center text-[11px] font-semibold text-text-primary">
                    {fmt(step.value)}
                  </span>
                </div>
                <div className="flex w-24 shrink-0 items-center justify-end gap-1 text-[11px]">
                  <TrendArrow delta={deltaPct} />
                  <span
                    className={
                      deltaPct > 0
                        ? "text-status-success"
                        : deltaPct < 0
                        ? "text-status-critical"
                        : "text-text-muted"
                    }
                  >
                    {deltaPct > 0 ? "+" : ""}
                    {deltaPct.toFixed(1)}%
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Consultation vs self-serve */}
      <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-primary" aria-hidden />
          <h2 className="text-sm font-semibold text-text-primary">Consultation-led vs self-serve quotes</h2>
        </div>
        <p className="mt-1 text-xs text-text-secondary">
          Buyers can flag on the RFQ form whether they want a consultation. Those quotes convert almost 2× as often  -  and get first-touched much faster.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {PORTFOLIO_CONSULTATION_MIX.map((row) => (
            <div key={row.intent} className="rounded-lg border border-border bg-surface-secondary/40 p-3">
              <p className="text-xs font-semibold text-text-primary">{row.intent}</p>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <p className="text-[10px] font-medium uppercase text-text-muted">Quotes</p>
                  <p className="text-sm font-semibold text-text-primary">{fmt(row.quotes)}</p>
                </div>
                <div>
                  <p className="text-[10px] font-medium uppercase text-text-muted">Win rate</p>
                  <p className="text-sm font-semibold text-text-primary">{pct(row.win_rate)}</p>
                </div>
                <div>
                  <p className="text-[10px] font-medium uppercase text-text-muted">Avg deal</p>
                  <p className="text-sm font-semibold text-text-primary">${fmt(row.avg_deal_size)}</p>
                </div>
                <div>
                  <p className="text-[10px] font-medium uppercase text-text-muted">Time to first touch</p>
                  <p className="text-sm font-semibold text-text-primary">{row.follow_up_hours} h</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Category leaderboard */}
      <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <ListOrdered className="h-4 w-4 text-primary" aria-hidden />
          <h2 className="text-sm font-semibold text-text-primary">Category leaderboard</h2>
        </div>
        <p className="mt-1 text-xs text-text-secondary">
          Which product categories are driving demand and pipeline this week.
        </p>
        <div className="mt-4 overflow-x-auto rounded-lg border border-border">
          <table className="min-w-full divide-y divide-border text-left text-sm">
            <thead className="bg-surface-secondary">
              <tr>
                <th className="px-3 py-2 font-semibold text-text-secondary">Category</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Views</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">RFQs</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Pipeline</th>
                <th className="px-3 py-2 font-semibold text-text-secondary">Week-over-week</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {PORTFOLIO_CATEGORY_LEADERBOARD.map((r) => (
                <tr key={r.category}>
                  <td className="px-3 py-2 font-medium text-text-primary">{r.category}</td>
                  <td className="px-3 py-2 tabular-nums text-text-secondary">{fmt(r.views)}</td>
                  <td className="px-3 py-2 tabular-nums text-text-secondary">{fmt(r.rfqs)}</td>
                  <td className="px-3 py-2 tabular-nums text-text-secondary">${fmt(r.pipeline_k)}K</td>
                  <td className="px-3 py-2 tabular-nums">
                    <span
                      className={`inline-flex items-center gap-0.5 text-xs ${
                        r.wow_pct > 0
                          ? "text-status-success"
                          : r.wow_pct < 0
                          ? "text-status-critical"
                          : "text-text-muted"
                      }`}
                    >
                      <TrendArrow delta={r.wow_pct} />
                      {r.wow_pct > 0 ? "+" : ""}
                      {r.wow_pct.toFixed(1)}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="rounded-xl border border-border bg-surface-secondary/40 p-4">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-text-primary">
          <HelpCircle size={12} className="text-primary" />
          Why these sections exist
        </p>
        <p className="mt-1 text-xs text-text-secondary">
          These panels mirror what stakeholders are used to seeing in the McFadyen Power BI reports  - 
          Traffic mix, Form funnel, Consultation vs self-serve, and Category leaderboards. In production,
          each panel would refresh from GA4 + HubSpot + LogRocket in the intervals set in the Data
          Readiness reference; for now the numbers are Middleby-sample-informed synthetic to show shape.
        </p>
      </div>
    </>
  );
}
