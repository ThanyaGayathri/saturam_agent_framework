import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import Breadcrumb from "./shared/Breadcrumb";
import { useBreadcrumbTrail } from "../hooks/useBreadcrumbTrail";
import {
  Sparkles,
  ArrowRight,
  Filter,
  TrendingUp,
  TrendingDown,
  Target,
} from "lucide-react";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { parseUseCaseProcessIds } from "../domain/use-case-process-links";
import { DemoAreaSpark, DemoDualLineSpark, DemoMiniDeltaSpark, DemoComboSpark, formatChartNumber } from "./shared/DemoSparkCharts";
import { getPlaybook, getPlaybooksForCatalogId } from "../domain/automation-registry";
import { getKpiNamesForCatalogId } from "../domain/kpi-usecase-links";
import { useDemoProfile } from "../context/DemoProfileContext";

function StatCard({ value, label }) {
  return (
    <div className="rounded-xl border border-border-light bg-surface px-5 py-4 shadow-sm">
      <div className="text-[13px] font-semibold text-text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight text-text-primary">{value}</div>
      <div className="mt-2 h-1 w-[70%] rounded-full bg-[#f0f0ff]" />
    </div>
  );
}


// â”€â”€ Executive KPI helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function kpiHash(key) {
  const s = String(key);
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 1000) / 1000;
}

function slugifyArea(label = "") {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

function flattenKpis(registry) {
  const out = [];
  for (const cat of registry?.categories || []) {
    for (const sec of cat?.sections || []) {
      for (const kpi of sec?.kpis || []) {
        if (kpi?.active !== false) out.push(kpi);
      }
    }
  }
  return out;
}

function getRelatedKpis(allKpis, entry, limit = 6) {
  if (!entry || !allKpis.length) return [];
  const areaId = slugifyArea(entry.businessArea || "");
  const processIds = parseUseCaseProcessIds(entry);
  const scored = allKpis.map((kpi) => {
    let score = 0;
    if (kpi.businessAreaId === areaId) score += 2;
    const mapping = Array.isArray(kpi.processMapping) ? kpi.processMapping : [];
    if (processIds.some((pid) => mapping.includes(pid))) score += 3;
    return { kpi, score };
  });
  return scored
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(({ kpi }) => kpi);
}

function mockMetric(kpi) {
  const h = kpiHash(kpi.name + "val");
  const hd = kpiHash(kpi.name + "delta");
  const benchmark = String(kpi.industryBenchmark || "");
  const isLowerBetter = benchmark.startsWith("<");
  const unit = String(kpi.unit || "");
  const numMatch = benchmark.match(/[\d.]+/);
  // Use Number.isFinite here — parseFloat("Rank vs.…") on a benchmark that
  // has no numeric target gives NaN, and NaN != null is true, so the
  // downstream `target != null` branch was letting NaN into every product
  // and producing "NaN%" tiles on the Impact dashboard.
  const parsedTarget = numMatch ? parseFloat(numMatch[0]) : null;
  const target = Number.isFinite(parsedTarget) ? parsedTarget : null;

  if (unit === "%") {
    const spread = target != null ? target * 0.45 : 20;
    const base = target != null
      ? (isLowerBetter ? target + spread * h : target - spread * h)
      : 55 + h * 35;
    const val = Math.max(0.1, Math.min(99.9, base));
    const delta = (isLowerBetter ? -1 : 1) * hd * spread * 0.45;
    return {
      value: `${val.toFixed(1)}%`,
      delta: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}pp`,
      improving: isLowerBetter ? delta < 0 : delta > 0,
    };
  }
  if (unit === "Days") {
    const base = target != null ? target + (isLowerBetter ? target * h * 0.6 : -target * h * 0.3) : 5 + h * 20;
    const val = Math.max(1, base);
    const delta = (isLowerBetter ? -1 : 1) * hd * val * 0.18;
    return {
      value: `${val.toFixed(1)}d`,
      delta: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}d`,
      improving: isLowerBetter ? delta < 0 : delta > 0,
    };
  }
  if (unit === "Units/hr" || unit === "Turns/yr") {
    const base = target != null ? target * (0.78 + h * 0.25) : 10 + h * 60;
    const delta = hd * base * 0.12;
    return { value: `${Math.round(base)}`, delta: `+${Math.round(delta)}`, improving: true };
  }
  if (unit === "$") {
    const base = 700000 + h * 500000;
    const delta = hd * 60000;
    return { value: `$${(base / 1000).toFixed(0)}K`, delta: `+$${(delta / 1000).toFixed(0)}K`, improving: true };
  }
  const val = 60 + h * 35;
  return { value: `${val.toFixed(1)}`, delta: `+${(hd * 5).toFixed(1)}`, improving: true };
}

function mockTrend(kpi, points = 7) {
  const isLowerBetter = String(kpi.industryBenchmark || "").startsWith("<");
  const base = kpiHash(kpi.name + "trend") * 0.6 + 0.2;
  return Array.from({ length: points }, (_, i) => {
    const noise = kpiHash(kpi.name + i) * 0.25 - 0.125;
    const progress = i / (points - 1);
    const trend = isLowerBetter ? -progress * 0.35 : progress * 0.35;
    return Math.max(0.05, Math.min(0.95, base + trend + noise));
  });
}


function ExecutiveKpiPanel({ entry, registry, kpiTargetNames = null }) {
  const allKpis = useMemo(() => flattenKpis(registry), [registry]);
  const related = useMemo(() => {
    // If the use case has a registered playbook, its kpiTargets name the
    // exact KPIs that automation moves. Prefer those over the broader
    // business-area / process match. Fall back to the generic filter when
    // no explicit targets are supplied (non-playbook use cases).
    if (Array.isArray(kpiTargetNames) && kpiTargetNames.length > 0) {
      const wanted = new Set(kpiTargetNames.map((n) => String(n).trim().toLowerCase()));
      const matched = allKpis.filter((k) => wanted.has(String(k.name || "").trim().toLowerCase()));
      if (matched.length > 0) return matched;
    }
    return getRelatedKpis(allKpis, entry);
  }, [allKpis, entry, kpiTargetNames]);

  if (!related.length) return null;

  return (
    <div className="mt-10">
      <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">KPI IMPACT</div>
      <p className="mt-2 text-sm leading-relaxed text-text-secondary">
        Key metrics this use case is designed to move. Trends show agent-driven improvement over the selected period.
      </p>
      <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {related.map((kpi) => {
          const metric = mockMetric(kpi);
          const trend = mockTrend(kpi);
          const deltaColor = metric.improving ? "bg-[#ecfdf3] text-[#067647]" : "bg-[#fef3f2] text-[#b42318]";
          return (
            <div key={kpi.name} className="rounded-xl border border-border-light bg-surface p-4 shadow-sm">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-semibold leading-snug text-text-primary">{kpi.name}</p>
                <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${deltaColor}`}>
                  {metric.improving ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                  {metric.delta}
                </span>
              </div>
              <div className="mt-2 text-2xl font-semibold tracking-tight text-text-primary">{metric.value}</div>
              <div className="mt-2 w-full min-w-0">
                <DemoMiniDeltaSpark
                  data={trend}
                  improving={metric.improving}
                  viewHeight={32}
                  maxHeight={76}
                  strokeWidth={1.9}
                  dotRadius={0.72}
                  dotStrokeWidth={0.72}
                />
              </div>
              {kpi.industryBenchmark ? (
                <div className="mt-2 flex items-center gap-1 text-[11px] text-text-muted">
                  <Target size={11} />
                  <span>Benchmark: {kpi.industryBenchmark}</span>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function UseCaseImpactDashboard() {
  const trail = useBreadcrumbTrail("Impact Dashboard", [{ label: "Playground", to: "/playground" }, { label: "Use Cases", to: "/use-cases" }]);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const catalogId = searchParams.get("catalogId");
  const mode = searchParams.get("mode") === "template" ? "template" : "deployed";
  // KPI target names for the Executive KPI panel. Merges the direct-map
  // (KPI_LINKS_BY_CATALOG_ID via getKpiNamesForCatalogId) with any real
  // automation workflow kpiTargets on this use case. Direct map is the
  // canonical source; workflow targets contribute their delta narrative on
  // the workflow page but supplement the panel too.
  const playbookKpiTargetNames = useMemo(() => {
    if (!catalogId) return null;
    const names = new Set(getKpiNamesForCatalogId(catalogId, 20));
    for (const pb of getPlaybooksForCatalogId(catalogId)) {
      const pbFull = getPlaybook(pb.id);
      for (const t of pbFull?.kpiTargets || []) {
        if (t?.name) names.add(t.name);
      }
    }
    return names.size > 0 ? Array.from(names) : null;
  }, [catalogId]);
  const { entries, hydrated } = useUseCaseCatalog();
  const { isMediaProfile } = useDemoProfile();

  const { data: registry } = useKpiRegistry();
  const [useCaseKey, setUseCaseKey] = useState("uc1");
  const [timeframe, setTimeframe] = useState("30d");
  const [activated, setActivated] = useState(false);

  const launchedEntry = useMemo(() => {
    if (!catalogId || !entries?.length) return null;
    return entries.find((e) => e.id === catalogId) ?? null;
  }, [catalogId, entries]);

  const datasets = useMemo(() => {
    const base = {
      uc1: {
        name: "Dashboard",
        badgeValue: "+14%",
        badgeLabel: "Efficiency gain",
        savedValue: "4.3m",
        savedLabel: "Time saved",
        accValue: "96%",
        accLabel: "Accuracy",
        overview: "Your AI agent is continuously optimizing maintenance workflows by highlighting delays, prioritizing urgent tasks, and improving data consistency across signals.",
        charts: {
          efficiency: {
            "7d": [10, 11, 12, 13, 13, 14, 15],
            "30d": [10, 11, 12.5, 13, 14.8, 16, 17.8],
          },
          handle: {
            "7d": [22, 21, 20, 19.2, 18.8, 18, 17.6],
            "30d": [22, 21, 20, 18.8, 17.5, 16.6, 15.4],
          },
          correlation: {
            "7d": { a: [70, 72, 74, 75, 76, 77, 78], b: [70, 69, 68, 67, 66, 65, 64] },
            "30d": { a: [70, 73, 75, 77, 79, 80, 82], b: [70, 68, 66, 65, 63, 61, 59] },
          },
        },
        bottleneck: {
          current: "4.3h",
          target: "1.0h",
          save: "3.3h",
          rootCause: "Manual search through missing location tracking. Avg 2-3 mins/item during backroom locate.",
        },
      },
      uc2: {
        name: "Retail Inventory Orchestration Dashboard",
        badgeValue: "+9%",
        badgeLabel: "Efficiency gain",
        savedValue: "3.1m",
        savedLabel: "Time saved",
        accValue: "93%",
        accLabel: "Accuracy",
        overview: "Impact over time: improved replenishment routing reduced stockouts, cut receiving delays, and strengthened prediction consistency.",
        charts: {
          efficiency: {
            "7d": [10, 10.6, 11.5, 12.3, 12.9, 13.2, 14.1],
            "30d": [10, 10.9, 12.0, 12.8, 13.7, 14.4, 15.1],
          },
          handle: {
            "7d": [20.5, 20.1, 19.6, 19.0, 18.3, 18.0, 17.8],
            "30d": [20.5, 20.0, 19.0, 18.1, 17.2, 16.8, 16.2],
          },
          correlation: {
            "7d": { a: [68, 70, 71, 72, 73, 74, 75], b: [70, 69, 68, 67, 66, 65, 64] },
            "30d": { a: [68, 70, 72, 74, 76, 77, 79], b: [70, 68, 67, 65, 63, 62, 60] },
          },
        },
        bottleneck: {
          current: "3.8h",
          target: "1.2h",
          save: "2.6h",
          rootCause: "Slow carrier handoff for split shipments; missing SLAs on exception routing.",
        },
      },
    };

    return base[useCaseKey];
  }, [timeframe, useCaseKey]);

  const active = datasets;
  const timeframeData = active.charts;
  const efficiency = timeframeData.efficiency[timeframe] ?? timeframeData.efficiency["30d"];
  const handle = timeframeData.handle[timeframe] ?? timeframeData.handle["30d"];
  const corr = timeframeData.correlation[timeframe] ?? timeframeData.correlation["30d"];

  // Fallback entry for KPI matching when no catalogId is in the URL
  const kpiEntry = launchedEntry ?? {
    businessArea: useCaseKey === "uc2" ? "Orders" : "Inventory",
    process: useCaseKey === "uc2" ? "P2" : "P1",
  };

  const headline = launchedEntry?.title ?? active.name;
  const overviewText = launchedEntry
    ? `Impact over time for "${launchedEntry.title}". Charts show representative demo trends; connect live data in Configure for production-grade monitoring.`
    : active.overview;

  return (
    <div className="mx-auto max-w-[1440px] px-8 py-10">
      <Breadcrumb items={trail} />
      {catalogId && hydrated ? (
        <div
          className={`mb-6 rounded-xl border px-4 py-3 text-sm ${
            launchedEntry
              ? "border-primary-light bg-primary-light/35 text-text-primary"
              : "border-border-light bg-surface-secondary text-text-secondary"
          }`}
        >
          {launchedEntry ? (
            <span>
              You're viewing <span className="font-semibold">impact over time</span> for{" "}
              <span className="font-semibold">"{launchedEntry.title}"</span>, the use case you just launched. The
              charts use sample trend data for this demo; hook up live data in Configure for real metrics.
            </span>
          ) : (
            <span>
              This page was opened from a launch link, but we couldn't find that saved use case (for example, if
              browser data was cleared). The charts below show <span className="font-semibold">sample</span> trends.
              Open <span className="font-semibold">Use case templates</span> to work with your saved use cases.
            </span>
          )}
        </div>
      ) : null}

      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-[260px]">
          <div className="inline-flex items-center gap-2 rounded-full bg-[#ecfdf3] px-2.5 py-1 text-xs font-semibold text-[#067647]">
            <span className="inline-flex h-2 w-2 rounded-full bg-[#12b76a]" />
            AI AGENT ACTIVE
          </div>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight text-text-primary">{headline}</h1>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-text-secondary">{overviewText}</p>

          {/* Action bar - Run/Edit live on the use case detail page
              (Activation Flow). Ask AI hidden until the backend is wired up. */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {catalogId ? (
              <button
                type="button"
                onClick={() => navigate(`/use-cases/activate/${encodeURIComponent(catalogId)}`)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border-light bg-surface px-3.5 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
              >
                <ArrowRight size={14} />
                Go to use case
              </button>
            ) : null}
          </div>
        </div>

        <div className="flex flex-col gap-3 sm:min-w-[420px]">
          <div className="flex flex-wrap items-center justify-end gap-2">
            {[
              { key: "7d", label: "Last 7 days" },
              { key: "30d", label: "Last 30 days" },
            ].map((t) => (
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

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <StatCard value={active.badgeValue} label={active.badgeLabel} />
            <StatCard value={active.savedValue} label={active.savedLabel} />
            <StatCard value={active.accValue} label={active.accLabel} />
          </div>
        </div>
      </div>

      {/* Run History strip */}
      <div className="mt-8 rounded-xl border border-border bg-surface p-4 shadow-sm">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Run history</span>
          <span className="text-xs text-text-muted">- impact measured across runs</span>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {(isMediaProfile
            ? [
                { run: 1, date: "Apr 1", outcome: "18 themes surfaced" },
                { run: 2, date: "Apr 8", outcome: "23 themes surfaced" },
                { run: 3, date: "Apr 15", outcome: "31 themes surfaced" },
                { run: 4, date: "Apr 18", outcome: "23 themes surfaced" },
              ]
            : [
                { run: 1, date: "Apr 1", outcome: "18 SKUs actioned" },
                { run: 2, date: "Apr 8", outcome: "23 SKUs actioned" },
                { run: 3, date: "Apr 15", outcome: "31 SKUs actioned" },
                { run: 4, date: "Apr 18", outcome: "23 SKUs actioned" },
              ]
          ).map((r, i, arr) => (
            <div key={r.run} className="flex items-center gap-0">
              <div className="flex flex-col items-center">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#ecfdf3] text-xs font-bold text-[#067647]">
                  {r.run}
                </div>
                <p className="mt-1 text-[10px] font-medium text-text-secondary">{r.date}</p>
                <p className="text-[10px] text-text-muted">{r.outcome}</p>
              </div>
              {i < arr.length - 1 && <div className="mx-2 mb-4 h-px w-8 bg-[#a7f3d0]" />}
            </div>
          ))}
          <div className="ml-2 flex items-center gap-1.5 rounded-full border border-dashed border-border-light px-3 py-1.5 text-xs text-text-muted">
            Next run scheduled
          </div>
        </div>
      </div>

      {/* Impact Overview and Process Health & Latency removed - both were
          generic template placeholders (efficiency %, handling time, accuracy
          vs latency) that were not tied to the specific use case. The KPIs
          panel below sources per-use-case KPIs from the registry. */}
      <div style={{ display: "none" }}>
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">IMPACT OVERVIEW</div>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-text-secondary">
              Here's a clear summary of how the agent is influencing operations, improving throughput, and reducing delays.
              All trends include simple explanations so you know exactly what they mean and why they matter.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-text-muted" />
            <div className="text-sm font-semibold text-text-tertiary">Use case</div>
            <select
              value={useCaseKey}
              onChange={(e) => setUseCaseKey(e.target.value)}
              className="rounded-lg border border-border-light bg-surface px-3 py-2 text-sm font-semibold text-text-tertiary"
            >
              <option value="uc1">Use case 1</option>
              <option value="uc2">Use case 2</option>
            </select>
          </div>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-5 md:grid-cols-3">
          <div className="min-w-0 rounded-2xl border border-border-light bg-surface p-5 shadow-sm">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-sm font-semibold text-text-primary">Agent-Driven Efficiency Over Time</div>
                <div className="mt-1 text-xs font-semibold text-text-muted">Weekly improvement trends</div>
              </div>
              <Sparkles className="h-5 w-5 text-primary" />
            </div>
            <div className="mt-4 w-full min-w-0">
              <DemoComboSpark
                data={efficiency}
                fillOpacityTop={0.22}
                viewHeight={52}
                maxHeight={280}
                strokeWidth={2.45}
                dotRadius={1.15}
                dotStrokeWidth={0.95}
                formatPoint={(v) => `${formatChartNumber(v)}%`}
              />
            </div>
            <div className="mt-3 text-xs text-text-secondary">
              The agent steadily improved efficiency by <span className="font-semibold text-primary">{active.badgeValue}</span> over the selected period.
            </div>
          </div>

          <div className="min-w-0 rounded-2xl border border-border-light bg-surface p-5 shadow-sm">
            <div className="text-sm font-semibold text-text-primary">Impact on Average Handling Time</div>
            <div className="mt-1 text-xs font-semibold text-text-muted">Before & after improvements</div>
            <div className="mt-4 w-full min-w-0">
              <DemoAreaSpark
                data={handle}
                stroke="#8b5cf6"
                fillOpacityTop={0.16}
                viewHeight={52}
                maxHeight={280}
                strokeWidth={2.45}
                dotRadius={1.15}
                dotStrokeWidth={1}
                formatPoint={(v) => `${formatChartNumber(v)} min`}
              />
            </div>
            <div className="mt-3 text-xs text-text-secondary">Handling time decreased due to fewer manual exception steps.</div>
          </div>

          <div className="min-w-0 rounded-2xl border border-border-light bg-surface p-5 shadow-sm">
            <div className="text-sm font-semibold text-text-primary">Accuracy vs Latency Correlation</div>
            <div className="mt-1 text-xs font-semibold text-text-muted">Quality and speed tradeoff</div>
            <div className="mt-4 w-full min-w-0">
              <DemoDualLineSpark
                a={corr.a}
                b={corr.b}
                viewHeight={52}
                maxHeight={280}
                dotRadius={1.05}
                dotStrokeWidth={0.92}
                seriesLabels={["Accuracy", "Latency"]}
                formatPointA={(v) => `${formatChartNumber(v)}%`}
                formatPointB={(v) => `${formatChartNumber(v)}%`}
              />
            </div>
            <div className="mt-3 text-xs text-text-secondary">
              The agent discovered that improving accuracy can also reduce latency by <span className="font-semibold text-primary">3-5%</span> on average.
            </div>
          </div>
        </div>
      </div>

      <ExecutiveKpiPanel
        entry={kpiEntry}
        registry={registry}
        kpiTargetNames={playbookKpiTargetNames}
      />

    </div>
  );
}
