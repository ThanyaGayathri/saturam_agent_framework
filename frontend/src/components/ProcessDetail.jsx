import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  ChartLine,
  ClipboardCheck,
  Clock,
  Database,
  Headphones,
  Info,
  PackageCheck,
  Repeat,
  Sparkles,
  Store,
  Tag,
  TrendingUp,
  Users,
  XCircle,
  Package,
  ShoppingCart,
  Monitor,
  Calendar,
} from "lucide-react";
import { useProcesses } from "../hooks/useProcesses";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useCasesForProcessId } from "../domain/use-case-process-links";
import { useCaseActivationPath } from "../domain/automation-registry";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import {
  getProcessAutomationStatus,
  getProcessConnectedSources,
  getProcessExceptionsCount,
  getProcessInsights,
  getProcessLastRunLabel,
  getProcessRunHistory,
  getProcessSuccessRate,
  getProcessTotalRuns,
  getStatusMeta,
} from "../domain/process-demo-metadata";

const PROCESS_ICONS = { PackageCheck, ClipboardCheck, Repeat, Tag, Store };

const BUSINESS_AREA_ICONS = {
  inventory: Package,
  orders: ShoppingCart,
  merchandise: Tag,
  "store-ops": Store,
  "e-com": ChartLine,
  pos: Monitor,
  support: Headphones,
  pricing: TrendingUp,
  finance: BarChart3,
  workforce: Users,
};

const OUTCOME_BADGE = {
  success: { label: "Success", icon: CheckCircle2, text: "#027a48", bg: "#ecfdf3", border: "#abefc6" },
  warning: { label: "Review", icon: AlertTriangle, text: "#b54708", bg: "#fffaeb", border: "#fedf89" },
  error: { label: "Exception", icon: XCircle, text: "#b42318", bg: "#fef3f2", border: "#fecdca" },
};

const INSIGHT_TONE = {
  warning: { icon: AlertTriangle, text: "#b54708", bg: "#fffaeb", border: "#fedf89", iconBg: "#fef0c7" },
  info: { icon: Info, text: "#175cd3", bg: "#eff8ff", border: "#b2ddff", iconBg: "#d1e9ff" },
  success: { icon: Sparkles, text: "#027a48", bg: "#ecfdf3", border: "#abefc6", iconBg: "#d1fadf" },
};

function flattenKpis(registry) {
  const out = [];
  for (const cat of registry?.categories || []) {
    for (const sec of cat.sections || []) {
      for (const k of sec.kpis || []) out.push({ ...k, _section: sec.name, _category: cat.category });
    }
  }
  return out;
}

function fakeKpiValue(kpi) {
  let h = 0;
  const s = `${kpi.name || ""}-${kpi._section || ""}`;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  const abs = Math.abs(h);
  if (kpi.unit === "%") return `${(60 + (abs % 40)).toFixed(1)}%`;
  if (kpi.unit === "$") return `$${(120 + (abs % 480)).toLocaleString()}K`;
  if (kpi.unit === "Days") return `${(2 + (abs % 28))} days`;
  if (kpi.unit === "Score") return `${(3.8 + (abs % 12) / 10).toFixed(1)} / 5`;
  if (kpi.unit === "Minutes") return `${(2 + (abs % 12))} min`;
  if (kpi.unit === "Count") return `${(48 + (abs % 920)).toLocaleString()}`;
  return `${(20 + (abs % 80))}`;
}

function fakeKpiTrend(kpi) {
  let h = 0;
  for (const c of `${kpi.name || ""}-trend`) h = (h * 31 + c.charCodeAt(0)) | 0;
  const dir = (Math.abs(h) % 3) - 1;
  if (dir === 0) return { delta: "Flat", direction: "flat" };
  const pct = (0.4 + (Math.abs(h) % 38) / 10).toFixed(1);
  return dir > 0 ? { delta: `+${pct}%`, direction: "up" } : { delta: `-${pct}%`, direction: "down" };
}

export default function ProcessDetail() {
  const { processId } = useParams();
  const navigate = useNavigate();
  const { processes, hydrated: processesHydrated } = useProcesses();
  const { entries: useCases, hydrated: catalogHydrated } = useUseCaseCatalog();
  const { data: kpiRegistry, hydrated: kpiHydrated } = useKpiRegistry();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();

  const process = useMemo(
    () => (processes || []).find((p) => p.id === processId),
    [processes, processId]
  );
  const businessArea = useMemo(
    () => BUSINESS_AREAS.find((b) => b.id === process?.businessAreaId),
    [process, BUSINESS_AREAS]
  );
  const linkedUseCases = useMemo(
    () => (process ? useCasesForProcessId(useCases || [], process.id) : []),
    [useCases, process]
  );
  const linkedKpis = useMemo(() => {
    if (!process) return [];
    return flattenKpis(kpiRegistry).filter((k) =>
      Array.isArray(k.processMapping) && k.processMapping.includes(process.id)
    );
  }, [kpiRegistry, process]);

  if (processesHydrated && !process) {
    return (
      <div className="mx-auto max-w-[1200px] px-5 py-10 sm:px-8">
        <button
          type="button"
          onClick={() => navigate("/processes")}
          className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
        >
          <ArrowLeft size={14} /> Back to Processes
        </button>
        <div className="mt-6 rounded-xl border border-border bg-surface p-8 text-center">
          <p className="text-base font-semibold text-text-primary">Process not found</p>
          <p className="mt-1 text-sm text-text-secondary">The process id {`"${processId}"`} does not exist in this workspace.</p>
        </div>
      </div>
    );
  }

  if (!processesHydrated || !catalogHydrated || !kpiHydrated || !process) {
    return (
      <div className="mx-auto max-w-[1200px] px-5 py-10 sm:px-8">
        <p className="text-sm text-text-secondary">Loading process…</p>
      </div>
    );
  }

  const status = getProcessAutomationStatus(linkedUseCases.length);
  const statusMeta = getStatusMeta(status);
  const ProcessIcon = PROCESS_ICONS[process.iconKey] || PackageCheck;
  const AreaIcon = BUSINESS_AREA_ICONS[process.businessAreaId] || Package;
  const lastRun = getProcessLastRunLabel(process.id);
  const totalRuns = getProcessTotalRuns(process.id);
  const successRate = getProcessSuccessRate(process.id);
  const exceptions = getProcessExceptionsCount(process.id);
  const runHistory = getProcessRunHistory(process, linkedUseCases);
  const insights = getProcessInsights(process);
  const sources = getProcessConnectedSources(process);

  const openUseCase = (uc) => {
    if (uc.source === "user") {
      const path = uc.status === "draft"
        ? (uc.resumePath || "/use-cases/builder/step-1").split("?")[0]
        : "/use-cases/builder/step-8";
      navigate(`${path}?reopenCatalogId=${encodeURIComponent(uc.id)}`);
    } else {
      navigate(useCaseActivationPath(uc.id));
    }
  };

  return (
    <div className="min-h-full w-full bg-transparent">
      <div className="mx-auto w-full max-w-[1200px] px-5 py-6 sm:px-8">
        <button
          type="button"
          onClick={() => navigate("/processes")}
          className="inline-flex items-center gap-1.5 text-sm font-semibold text-text-tertiary transition hover:text-primary"
        >
          <ArrowLeft size={14} /> Back to Processes
        </button>

        <header className="mt-4 rounded-2xl border border-border bg-surface p-5 shadow-sm sm:p-7">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-primary-light">
              <ProcessIcon size={26} className="text-primary" strokeWidth={1.85} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => navigate(`/processes#area-${process.businessAreaId}`)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-secondary px-2.5 py-1 text-xs font-medium text-text-tertiary transition hover:border-primary/30 hover:text-primary"
                >
                  <AreaIcon size={12} /> {businessArea?.label || process.businessAreaId}
                </button>
                <span className="rounded-full border border-border bg-surface-secondary px-2.5 py-1 text-xs font-medium text-text-tertiary">
                  {process.subjectArea}
                </span>
                <span
                  className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold"
                  style={{ background: statusMeta.bg, color: statusMeta.text, borderColor: statusMeta.border }}
                >
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: statusMeta.dot }} />
                  {statusMeta.label}
                </span>
              </div>
              <h1 className="mt-3 text-2xl font-semibold tracking-tight text-text-primary sm:text-[28px]">
                {process.name}
              </h1>
              <p className="mt-2 max-w-3xl text-sm leading-relaxed text-text-secondary sm:text-[15px]">
                {process.description}
              </p>
              {process.impact ? (
                <p className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-status-success-light px-2.5 py-1 text-xs font-medium text-status-success">
                  <Sparkles size={12} /> {process.impact}
                </p>
              ) : null}
            </div>
          </div>

          <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
            <StatCard label="Last analyzed" value={lastRun} icon={<Clock size={14} />} />
            <StatCard label="Total runs (30d)" value={totalRuns.toLocaleString()} icon={<Calendar size={14} />} />
            <StatCard label="Success rate" value={`${successRate}%`} icon={<CheckCircle2 size={14} />} accent="success" />
            <StatCard
              label="Exceptions open"
              value={exceptions}
              icon={<AlertTriangle size={14} />}
              accent={exceptions > 0 ? "warning" : "neutral"}
            />
          </div>
        </header>

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
          <section className="lg:col-span-2">
            <SectionHeader title="Recent analysis runs" subtitle="The last 6 AI runs that involved this process" />
            <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
              <table className="min-w-full divide-y divide-border">
                <thead className="bg-surface-secondary">
                  <tr>
                    <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-text-muted">When</th>
                    <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-text-muted">Use case</th>
                    <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-text-muted">Outcome</th>
                    <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-text-muted">Summary</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border bg-surface">
                  {runHistory.map((row) => {
                    const o = OUTCOME_BADGE[row.outcomeStatus] || OUTCOME_BADGE.success;
                    const OutcomeIcon = o.icon;
                    return (
                      <tr key={row.id} className="transition hover:bg-surface-secondary/50">
                        <td className="whitespace-nowrap px-4 py-3 text-sm text-text-secondary">{row.time}</td>
                        <td className="px-4 py-3 text-sm font-medium text-text-primary">{row.useCase}</td>
                        <td className="whitespace-nowrap px-4 py-3">
                          <span
                            className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold"
                            style={{ background: o.bg, color: o.text, borderColor: o.border }}
                          >
                            <OutcomeIcon size={11} /> {o.label}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm text-text-secondary">{row.summary}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="mt-8">
              <SectionHeader title="Recent insights" subtitle="What the AI flagged on this process recently" />
              <div className="mt-3 space-y-3">
                {insights.map((ins, idx) => {
                  const tone = INSIGHT_TONE[ins.tone] || INSIGHT_TONE.info;
                  const ToneIcon = tone.icon;
                  return (
                    <div
                      key={idx}
                      className="flex items-start gap-3 rounded-xl border p-4 shadow-sm"
                      style={{ background: tone.bg, borderColor: tone.border }}
                    >
                      <div
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
                        style={{ background: tone.iconBg }}
                      >
                        <ToneIcon size={15} style={{ color: tone.text }} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold" style={{ color: tone.text }}>{ins.title}</p>
                        <p className="mt-1 text-sm leading-relaxed text-text-secondary">{ins.body}</p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>

          <aside className="space-y-6">
            <div>
              <SectionHeader title="Linked use cases" subtitle={`${linkedUseCases.length} in the catalog`} />
              {linkedUseCases.length === 0 ? (
                <p className="mt-3 rounded-xl border border-dashed border-border bg-surface p-4 text-sm text-text-muted">
                  No use cases reference this process yet.
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {linkedUseCases.map((uc) => (
                    <li key={uc.id}>
                      <button
                        type="button"
                        onClick={() => openUseCase(uc)}
                        className="group flex w-full items-start justify-between gap-2 rounded-xl border border-border bg-surface p-3 text-left shadow-sm transition hover:border-primary/35 hover:shadow-md"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-text-primary group-hover:text-primary">{uc.title}</p>
                          {uc.problemSignal ? (
                            <p className="mt-0.5 line-clamp-2 text-xs text-text-tertiary">{uc.problemSignal}</p>
                          ) : null}
                        </div>
                        <ArrowRight size={14} className="mt-1 shrink-0 text-text-muted transition group-hover:text-primary" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <SectionHeader title="Linked KPIs" subtitle={`${linkedKpis.length} mapped to this process`} />
              {linkedKpis.length === 0 ? (
                <p className="mt-3 rounded-xl border border-dashed border-border bg-surface p-4 text-sm text-text-muted">
                  No KPIs mapped to this process yet.
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {linkedKpis.slice(0, 6).map((k) => {
                    const trend = fakeKpiTrend(k);
                    const trendColor =
                      trend.direction === "up" ? "#027a48" : trend.direction === "down" ? "#b42318" : "#475467";
                    return (
                      <li
                        key={`${k._category}-${k._section}-${k.name}`}
                        className="rounded-xl border border-border bg-surface p-3 shadow-sm"
                      >
                        <p className="text-sm font-semibold text-text-primary">{k.name}</p>
                        <div className="mt-1 flex items-baseline justify-between gap-2">
                          <span className="text-lg font-semibold tabular-nums text-text-primary">{fakeKpiValue(k)}</span>
                          <span className="text-xs font-semibold tabular-nums" style={{ color: trendColor }}>
                            {trend.delta}
                          </span>
                        </div>
                        <p className="mt-1 text-[11px] text-text-muted">
                          {k._section}
                          {k.level ? ` · ${k.level}` : ""}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div>
              <SectionHeader title="Connected data sources" subtitle="Feeds this process consumes" />
              <ul className="mt-3 space-y-2">
                {sources.map((s) => (
                  <li
                    key={s}
                    className="flex items-center gap-2 rounded-xl border border-border bg-surface p-3 text-sm text-text-primary shadow-sm"
                  >
                    <Database size={14} className="shrink-0 text-text-muted" />
                    <span className="truncate">{s}</span>
                  </li>
                ))}
              </ul>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

function SectionHeader({ title, subtitle }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <h2 className="text-base font-semibold text-text-primary sm:text-[17px]">{title}</h2>
      {subtitle ? <span className="text-xs text-text-muted">{subtitle}</span> : null}
    </div>
  );
}

function StatCard({ label, value, icon, accent = "neutral" }) {
  const tone =
    accent === "success"
      ? { value: "text-status-success", bg: "bg-status-success-light/40", border: "border-status-success-border/40" }
      : accent === "warning"
        ? { value: "text-[#b54708]", bg: "bg-[#fffaeb]", border: "border-[#fedf89]" }
        : { value: "text-text-primary", bg: "bg-surface-secondary", border: "border-border" };
  return (
    <div className={`rounded-xl border ${tone.border} ${tone.bg} p-3 shadow-sm sm:p-4`}>
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        <span className="text-text-muted">{icon}</span>
        {label}
      </p>
      <p className={`mt-1 text-lg font-semibold tabular-nums ${tone.value} sm:text-xl`}>{value}</p>
    </div>
  );
}
