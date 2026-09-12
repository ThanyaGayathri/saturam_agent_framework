import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  ArrowUp,
  ArrowDown,
  Minus,
  Sparkles,
  Target,
  TrendingUp,
  Zap,
} from "lucide-react";
import Breadcrumb from "./shared/Breadcrumb";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useDemoProfile } from "../context/DemoProfileContext";
import {
  getPlaybooksForProfile,
  playbooksTargetingKpiName,
} from "../domain/automation-registry";

const TIMEFRAMES = [
  { key: "7d", label: "Last 7 days", weeks: 1 },
  { key: "30d", label: "Last 30 days", weeks: 4 },
  { key: "quarter", label: "This quarter", weeks: 13 },
];

// Deterministic hash so mock values are stable across renders per KPI.
function stableHash(key) {
  const s = String(key || "");
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 1000) / 1000;
}

function flattenKpis(registry) {
  const out = [];
  for (const cat of registry?.categories || []) {
    for (const sec of cat?.sections || []) {
      for (const kpi of sec?.kpis || []) {
        if (kpi?.active !== false) {
          out.push({ ...kpi, categoryName: cat.category || "", sectionName: sec.name || "" });
        }
      }
    }
  }
  return out;
}

/**
 * Enterprise GPT Impact - the exec-view summary of how automation playbooks
 * are moving business KPIs. For each KPI in the current profile's registry,
 * shows current value + WoW change, which playbooks contribute, estimated
 * Enterprise GPT attribution, and most recent contributing runs.
 */
export default function EnterpriseGptImpact() {
  const { data: registry } = useKpiRegistry();
  const { profileId } = useDemoProfile();
  const [timeframe, setTimeframe] = useState("30d");
  const timeframeCfg = TIMEFRAMES.find((t) => t.key === timeframe) ?? TIMEFRAMES[1];

  const allKpis = useMemo(() => flattenKpis(registry), [registry]);
  const profilePlaybooks = useMemo(() => getPlaybooksForProfile(profileId), [profileId]);

  // Build the KPI x playbook attribution list. Only KPIs that at least one
  // profile playbook targets appear on this exec view; the KPI Registry
  // (linked below) already lists every KPI regardless of automation coverage.
  const kpiAttribution = useMemo(() => {
    return allKpis
      .map((kpi) => {
        const contributors = playbooksTargetingKpiName(kpi.name, profileId);
        return { kpi, contributors };
      })
      .filter((row) => row.contributors.length > 0);
  }, [allKpis, profileId]);

  // Summary tiles at top.
  const summary = useMemo(() => {
    const playbooksTargeting = profilePlaybooks.filter((p) => (p.kpiTargets || []).length > 0);
    const kpisTargeted = kpiAttribution.length;
    const kpisTotal = allKpis.length;
    const runsThisPeriod = Math.max(
      profilePlaybooks.length * timeframeCfg.weeks,
      profilePlaybooks.length,
    );
    // Rough attribution average across all targeted KPIs.
    const avgAttribution = kpiAttribution.length
      ? kpiAttribution.reduce((acc, row) => {
          const h = stableHash(row.kpi.name + "attr");
          return acc + Math.round(35 + h * 55);
        }, 0) / kpiAttribution.length
      : 0;
    return {
      playbooksActive: playbooksTargeting.length,
      playbooksTotal: profilePlaybooks.length,
      kpisTargeted,
      kpisTotal,
      runsThisPeriod,
      avgAttribution: Math.round(avgAttribution),
    };
  }, [profilePlaybooks, kpiAttribution, allKpis.length, timeframeCfg.weeks]);

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <Breadcrumb items={[{ label: "Enterprise GPT Impact" }]} />

      <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="inline-flex items-center gap-2 rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary dark:bg-primary/15">
            <Sparkles size={13} />
            Enterprise GPT Impact
          </div>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight text-text-primary sm:text-[26px]">
            How Enterprise GPT is moving your business
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-text-secondary">
            One view of every business KPI that active automation playbooks target.
            For each KPI you see the current value, its trend, which playbooks contribute,
            and the estimated share of movement attributable to Enterprise GPT.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {TIMEFRAMES.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTimeframe(t.key)}
              className={`rounded-lg border px-3 py-1.5 text-sm font-semibold transition ${
                timeframe === t.key
                  ? "border-primary bg-primary-light text-primary dark:border-primary/50 dark:bg-primary/15"
                  : "border-border-light bg-surface text-text-tertiary hover:bg-surface-secondary"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>

      {/* Summary tiles */}
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <SummaryTile
          icon={Zap}
          label="Active playbooks"
          value={summary.playbooksActive}
          sub={`of ${summary.playbooksTotal} configured`}
        />
        <SummaryTile
          icon={Target}
          label="KPIs targeted"
          value={summary.kpisTargeted}
          sub={`of ${summary.kpisTotal} in the registry`}
        />
        <SummaryTile
          icon={Activity}
          label={`Runs ${timeframeCfg.label.toLowerCase()}`}
          value={summary.runsThisPeriod}
          sub="Across all playbooks"
        />
        <SummaryTile
          icon={TrendingUp}
          label="Avg. attribution"
          value={`${summary.avgAttribution}%`}
          sub="Est. share Enterprise GPT drove"
        />
      </div>

      {kpiAttribution.length === 0 ? (
        <section className="mt-8 rounded-xl border border-border bg-surface-default p-8 text-center shadow-sm">
          <p className="text-sm text-text-secondary">
            No playbooks are targeting KPIs for this profile yet. Configure an automation via the
            Use Case Builder to see it here.
          </p>
          <Link
            to="/use-cases"
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-white hover:bg-primary-hover"
          >
            Go to use cases
            <ArrowRight size={14} />
          </Link>
        </section>
      ) : (
        <section className="mt-8 space-y-3">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
              KPIs powered by Enterprise GPT
            </h2>
            <p className="text-xs text-text-muted">
              {kpiAttribution.length} KPI{kpiAttribution.length === 1 ? "" : "s"}
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {kpiAttribution.map(({ kpi, contributors }) => (
              <KpiAttributionCard
                key={kpi.name}
                kpi={kpi}
                contributors={contributors}
                timeframe={timeframeCfg}
              />
            ))}
          </div>
        </section>
      )}

      {/* Footer link back to the registry */}
      <div className="mt-8 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-6 text-sm text-text-muted">
        <p>
          Values shown are estimates from sample data. Connect live sources in{" "}
          <Link to="/configure/internal-sources" className="text-primary hover:underline">
            Configure
          </Link>{" "}
          to replace with real metrics.
        </p>
        <Link to="/kpis" className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">
          Full KPI registry
          <ArrowRight size={13} />
        </Link>
      </div>
    </div>
  );
}

// ==========================================================================
// Summary tile
// ==========================================================================

function SummaryTile({ icon: Icon, label, value, sub }) {
  return (
    <div className="rounded-xl border border-border-light bg-surface px-5 py-4 shadow-sm">
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        <Icon size={13} className="text-primary" aria-hidden />
        {label}
      </div>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-text-primary">{value}</p>
      {sub ? <p className="mt-1 text-xs text-text-muted">{sub}</p> : null}
    </div>
  );
}

// ==========================================================================
// KPI attribution card
// ==========================================================================

function KpiAttributionCard({ kpi, contributors, timeframe }) {
  const metric = useMemo(() => mockKpiMetric(kpi), [kpi]);
  const attribution = useMemo(() => {
    const h = stableHash(kpi.name + "attr");
    return Math.round(35 + h * 55);
  }, [kpi.name]);
  const lastRunHours = useMemo(() => Math.max(1, Math.round(stableHash(kpi.name + "hrs") * 96)), [kpi.name]);
  const primary = contributors[0];

  return (
    <div className="flex flex-col rounded-2xl border border-border-light bg-surface p-5 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-text-primary">{kpi.name}</p>
          {kpi.sectionName || kpi.categoryName ? (
            <p className="mt-0.5 text-xs text-text-muted">
              {[kpi.categoryName, kpi.sectionName].filter(Boolean).join(" · ")}
            </p>
          ) : null}
        </div>
        <span
          className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
            metric.improving ? "bg-[#ecfdf3] text-[#067647]" : "bg-[#fef3f2] text-[#b42318]"
          }`}
        >
          {metric.improving ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
          {metric.delta}
        </span>
      </div>

      <div className="mt-3 flex items-baseline gap-2">
        <p className="text-3xl font-semibold tabular-nums text-text-primary">{metric.value}</p>
        <p className="text-xs text-text-muted">{timeframe.label.toLowerCase()}</p>
      </div>

      <div className="mt-4 space-y-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
            Powered by
          </p>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {contributors.map((p) => (
              <span
                key={p.id}
                className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-primary-light px-2 py-0.5 text-[11px] font-medium text-primary dark:bg-primary/15"
              >
                <Zap size={10} />
                {p.id} · {p.label}
              </span>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-secondary/40 px-3 py-2">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
              Enterprise GPT attribution
            </p>
            <p className="mt-0.5 text-sm font-medium text-text-primary">
              ~{attribution}% of movement attributable to Enterprise GPT
            </p>
          </div>
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <TrendingUp size={16} aria-hidden />
          </div>
        </div>

        {primary ? (
          <p className="text-xs text-text-muted">
            <span className="font-medium text-text-secondary">Last contributing run:</span>{" "}
            {primary.label} · {lastRunHours < 24 ? `${lastRunHours}h ago` : `${Math.round(lastRunHours / 24)}d ago`}
          </p>
        ) : null}
      </div>
    </div>
  );
}

// ==========================================================================
// Mock metric generator (stable per KPI name)
// ==========================================================================

function mockKpiMetric(kpi) {
  const h = stableHash(kpi.name + "val");
  const hd = stableHash(kpi.name + "delta");
  const benchmark = String(kpi.industryBenchmark || "");
  const isLowerBetter = benchmark.startsWith("<");
  const unit = String(kpi.unit || "");
  const numMatch = benchmark.match(/[\d.]+/);
  const target = numMatch ? parseFloat(numMatch[0]) : null;

  if (unit === "%") {
    const spread = target != null ? target * 0.45 : 20;
    const base =
      target != null
        ? isLowerBetter
          ? target + spread * h
          : target - spread * h
        : 55 + h * 35;
    const val = Math.max(0.1, Math.min(99.9, base));
    const delta = (isLowerBetter ? -1 : 1) * hd * spread * 0.45;
    return {
      value: `${val.toFixed(1)}%`,
      delta: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}pt`,
      improving: isLowerBetter ? delta < 0 : delta > 0,
    };
  }
  if (unit === "Days") {
    const base =
      target != null
        ? target + (isLowerBetter ? target * h * 0.6 : -target * h * 0.3)
        : 5 + h * 20;
    const val = Math.max(1, base);
    const delta = (isLowerBetter ? -1 : 1) * hd * val * 0.18;
    return {
      value: `${val.toFixed(1)}d`,
      delta: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}d`,
      improving: isLowerBetter ? delta < 0 : delta > 0,
    };
  }
  if (unit === "$" || unit === "USD") {
    const base = target != null ? target * (0.85 + h * 0.3) : 10000 + h * 50000;
    const delta = hd * base * 0.15;
    return {
      value: `$${Math.round(base).toLocaleString()}`,
      delta: `${delta > 0 ? "+" : ""}$${Math.abs(Math.round(delta)).toLocaleString()}`,
      improving: delta > 0,
    };
  }
  // Default: raw count
  const base = target != null ? target * (0.82 + h * 0.25) : 40 + h * 500;
  const delta = hd * base * 0.15;
  return {
    value: Math.round(base).toLocaleString(),
    delta: `${delta > 0 ? "+" : ""}${Math.round(delta).toLocaleString()}`,
    improving: delta > 0,
  };
}
