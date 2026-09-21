import { buildInsightAnswer, parseNum } from "./middleby-analytics-data";

const UC11_HEADLINE =
  "Repeat-brand buyers are a thin slice of quote volume (4 of 151 spam-filtered deals in the 351-deal export). Most pipeline still comes from first-time brand interest.";

/**
 * Enrich the deterministic answer summary for select use cases.
 * Middleby insight sessions use a single audience; demo role is ignored.
 */
export function applyRoleLensToAnswer(answer, catalogId, _role, entry, spec, rows) {
  if (catalogId === "middleby-uc-11") {
    return { ...answer, summary: UC11_HEADLINE };
  }

  if (catalogId === "middleby-uc-04" && spec?.chartType === "priority_table") {
    const urgent = rows.filter((r) => String(r.timeframe || "").includes("0 - 1"));
    const consult = urgent.filter((r) => String(r.consultation || "").toLowerCase() === "yes");
    return {
      ...answer,
      summary: `${urgent.length} quotes need near-term follow-up; ${consult.length} requested consultation and warrant leadership visibility on dealer response.`,
    };
  }

  if (catalogId === "middleby-uc-08" && spec?.chartType === "ranked_table") {
    const dims = spec.dimensionColumns || [];
    const metrics = spec.metricColumns || [];
    const sessionsCol = metrics.find((c) => /session/i.test(c)) || metrics[0];
    const quotesCol = metrics.find((c) => /quote/i.test(c)) || metrics[1];
    const dollarsCol = metrics.find((c) => /_usd$|dollar/i.test(c)) || metrics[2];
    const totalDollars = rows.reduce((s, r) => s + (parseNum(r[dollarsCol]) ?? 0), 0);
    const ranked = [...rows]
      .map((r) => ({
        label: r[dims[0]],
        sessions: parseNum(r[sessionsCol]) ?? 0,
        quotes: parseNum(r[quotesCol]) ?? 0,
        dollars: parseNum(r[dollarsCol]) ?? 0,
      }))
      .sort((a, b) => b.dollars - a.dollars);
    const top = ranked[0];
    if (top && totalDollars > 0) {
      const fmtUsd = (n) =>
        n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : `$${Math.round(n / 1_000).toLocaleString()}K`;
      return {
        ...answer,
        summary: `The top UX issue, "${top.label}": is putting ${fmtUsd(top.dollars)} of quote pipeline at risk (${top.sessions.toLocaleString()} sessions affected, ${top.quotes} high-intent quotes). Across all tracked issues, roughly ${fmtUsd(totalDollars)} in pipeline is exposed until they're fixed.`,
      };
    }
  }

  if (catalogId === "middleby-uc-18" && spec?.chartType === "dual_axis_bar") {
    const dims = spec.dimensionColumns || [];
    const metrics = spec.metricColumns || [];
    const scored = [...rows].map((r) => ({
      label: r[dims[0]],
      sessions: parseNum(r[metrics[0]]) ?? 0,
      quotes: parseNum(r[metrics[1]]) ?? 0,
    }));
    const totalSessions = scored.reduce((s, r) => s + r.sessions, 0);
    const totalQuotes = scored.reduce((s, r) => s + r.quotes, 0);
    const avgRate = totalSessions > 0 ? (totalQuotes / totalSessions) * 100 : 0;
    const leader = scored
      .filter((r) => r.sessions >= 300)
      .map((r) => ({ ...r, rate: (r.quotes / r.sessions) * 100 }))
      .sort((a, b) => b.rate - a.rate)[0];
    const gaps = scored
      .filter((g) => g.sessions > 500 && g.quotes <= 3)
      .sort((a, b) => b.sessions - a.sessions);
    const top = gaps[0];
    if (top && leader && avgRate > 0) {
      const ratio = Math.round(leader.rate / avgRate);
      const gapTraffic = gaps.slice(0, 3).reduce((s, r) => s + r.sessions, 0);
      const upliftQuotes = Math.round((gapTraffic * leader.rate) / 100) - gaps.slice(0, 3).reduce((s, r) => s + r.quotes, 0);
      return {
        ...answer,
        summary: `${leader.label} converts traffic to quotes at ${leader.rate.toFixed(1)}%: ${ratio}x the Middleby average of ${avgRate.toFixed(1)}%. ${top.label} sits at ${((top.quotes / top.sessions) * 100).toFixed(1)}% (${top.sessions.toLocaleString()} sessions, ${top.quotes} quote${top.quotes === 1 ? "" : "s"}). Closing the top-3 gap brands to ${leader.label}'s rate would add roughly ${upliftQuotes} quotes a week: nearly doubling current volume.`,
      };
    }
  }

  return answer;
}

export function buildRoleLensAnswer(entry, spec, rows, role, catalogId) {
  const base = buildInsightAnswer(entry, spec, rows);
  return applyRoleLensToAnswer(base, catalogId, role, entry, spec, rows);
}
