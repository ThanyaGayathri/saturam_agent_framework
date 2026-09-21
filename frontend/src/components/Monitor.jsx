// Monitor - operational health view. "Is everything running and is it
// improving the metrics we care about?" Aggregates the same data used by
// the Alerts inbox, KPI registry, use case catalog, and data-source config
// so nothing drifts out of sync. This is a demo view: numbers are derived
// deterministically from the underlying entities so they look stable.
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
 Activity,
 AlertTriangle,
 
 ArrowRight,
 ArrowUpRight,
 ArrowDownRight,
 Bell,
 CheckCircle2,
 ClipboardList,
 Database,
 GitBranch,
 LineChart,
 Zap,
} from "lucide-react";
import { useAlerts } from "../hooks/useAlerts";
import { fetchExecutionHealth } from "../services/use-cases-api";
import { cadenceLabel } from "../domain/use-case-platform";
import { useProcesses } from "../hooks/useProcesses";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useDemoProfile } from "../context/DemoProfileContext";
import { loadConfigureWorkspace } from "../storage/configure-workspace";
import {
 SEVERITY_META,
 getBusinessAreaLabel,
 relativeTimeFromNow,
 sortAlertsForDisplay,
} from "../domain/alerts-view";
import { normalizeSeriesYs, smoothLinePath, ResponsiveChartBox, ChartHoverWrap, formatChartNumber } from "./shared/DemoSparkCharts";
import TourLaunchButton from "../tours/TourLaunchButton";
const SOURCE_TYPE_LABELS = {
 postgres: "PostgreSQL",
 snowflake: "Snowflake",
 sap_s4hana: "SAP S/4HANA",
 salesforce: "Salesforce",
 bigquery: "BigQuery",
 mysql: "MySQL",
 mssql: "SQL Server",
 oracle: "Oracle",
 oracle_db: "Oracle",
 redshift: "Redshift",
 s3: "S3",
 azure_blob: "Azure Blob",
 rest_api: "REST API",
 netsuite: "NetSuite",
 dynamics365: "Dynamics 365",
 airflow: "Airflow",
 aws_glue: "AWS Glue",
};
/** Cheap deterministic hash → [0, 1) for stable "random" mock numbers per key. */
function hash01(key) {
 const s = String(key);
 let h = 2166136261;
 for (let i = 0; i < s.length; i += 1) {
 h ^= s.charCodeAt(i);
 h = Math.imul(h, 16777619);
 }
 return ((h >>> 0) % 1000) / 1000;
}
export default function Monitor() {
 const navigate = useNavigate();
 const { alerts, hydrated: alertsHydrated, unreadCount, criticalUnreadCount } = useAlerts();
 const { processes, hydrated: processesHydrated } = useProcesses();
 const { hydrated: kpiHydrated } = useKpiRegistry();
 // Read-only view of the Configure workspace data so we don't require the
 // ConfigureWorkspaceProvider to be mounted (it's scoped to /configure).
 const { storageKeys, profileId } = useDemoProfile();
 const [workspace, setWorkspace] = useState(null);
 useEffect(() => {
 setWorkspace(loadConfigureWorkspace(storageKeys.CONFIGURE_WORKSPACE, profileId));
 }, [storageKeys.CONFIGURE_WORKSPACE, profileId]);
 const workspaceHydrated = workspace !== null;
 // Execution health from the platform (K20): real runs, real schedules, real silence.
 const [health, setHealth] = useState(null);
 const [healthError, setHealthError] = useState(null);
 useEffect(() => {
 let alive = true;
 const load = () => fetchExecutionHealth().then((h) => { if (alive) { setHealth(h); setHealthError(null); } }).catch((err) => { if (alive) { setHealth({ as_of: null, use_cases: [] }); setHealthError(err?.status === 0 ? "The platform is not running." : err?.detail || err?.message); } });
 load();
 const t = setInterval(load, 30000);
 return () => { alive = false; clearInterval(t); };
 }, []);
 const platformUseCases = useMemo(() => health?.use_cases || [], [health]);
 const ready =
 alertsHydrated && processesHydrated && kpiHydrated && workspaceHydrated;
 const processById = useMemo(
 () => Object.fromEntries((processes || []).map((p) => [p.id, p])),
 [processes]
 );
 // Stat tiles for the header strip.
 const stats = useMemo(() => {
 const activeUseCases = platformUseCases.filter((u) => u.status === "activated").length;
 const runs7d = platformUseCases.reduce((n, u) => n + (u.runs_7d || 0), 0);
 const exceptions7d = platformUseCases.reduce((n, u) => n + (u.exceptions_7d || 0), 0);
 const created7d = platformUseCases.reduce((n, u) => n + (u.created_7d || 0), 0);
 const silent = platformUseCases.filter((u) => u.silent).length;
 const failed7d = platformUseCases.reduce((n, u) => n + (u.failures_7d || 0), 0);
 const criticalAlerts = alerts.filter((a) => a.status !== "resolved" && a.severity === "critical").length;
 const sources = workspace?.internalSources || [];
 const sourcesOk = sources.length;
 const external = (workspace?.externalIntegrations || []).filter((e) => e.status === "active");
 return {
 runs7d, exceptions7d, created7d, silent, failed7d,
 activeUseCases,
 criticalAlerts,
 openAlerts: alerts.filter((a) => a.status !== "resolved").length,
 sourcesOk,
 sourcesTotal: sources.length + external.length,
 avgLatencyMs: 620 + Math.round(hash01("latency") * 180),
 tasksToday: 1284 + Math.round(hash01("tasks") * 340),
 };
 }, [alerts, workspace, platformUseCases]);
 // Alerts sorted once for the "Needs attention" strip.
 const topOpenAlerts = useMemo(
 () => sortAlertsForDisplay(alerts.filter((a) => a.status !== "resolved")).slice(0, 5),
 [alerts]
 );
 // Activity feed: synthesise recent runs from processes + alerts so it
 // always has texture, even on a fresh demo reset.
 const activityFeed = useMemo(() => {
 const now = Date.now();
 const feed = [];
 alerts.slice(0, 10).forEach((a) => {
 feed.push({
 id: `alert-${a.id}`,
 at: a.createdAt,
 kind: "alert",
 severity: a.severity,
 title: a.title,
 tag: getBusinessAreaLabel(a.businessAreaId),
 processName: processById[a.processId]?.name || "",
 });
 });
 (processes || []).slice(0, 6).forEach((p, i) => {
 const offset = (i + 1) * 7 * 60 * 1000 + Math.round(hash01(p.id) * 8 * 60 * 1000);
 feed.push({
 id: `run-${p.id}-${i}`,
 at: now - offset,
 kind: "run",
 title: `${p.name} completed a routine run`,
 tag: "",
 processName: p.name,
 });
 });
 platformUseCases.forEach((uc) => {
 if (!uc.last_run_at) return;
 feed.push({
 id: `uc-${uc.use_case_id}`,
 at: new Date(uc.last_run_at).getTime(),
 kind: "use-case",
 title: `${uc.title} ${uc.last_run_status === "failed" ? "failed" : "ran"}${uc.last_run_summary ? `: ${uc.last_run_summary}` : ""}`,
 tag: uc.schedule ? cadenceLabel(uc.schedule) : "",
 processName: "",
 });
 });
 feed.sort((a, b) => b.at - a.at);
 return feed.slice(0, 12);
 }, [alerts, processes, processById, platformUseCases]);
 // KPI drift: top movers, derived from alerts that reference a KPI.
 const kpiDrift = useMemo(() => {
 const withKpi = alerts.filter((a) => a.kpi && a.kpi.name && a.status !== "resolved");
 return withKpi.slice(0, 5).map((a) => ({
 id: a.id,
 name: a.kpi.name,
 businessAreaId: a.businessAreaId,
 value: a.kpi.value,
 delta: a.kpi.delta || "",
 positive: !(a.kpi.delta || "").startsWith("-") ? a.severity === "info" : false,
 severity: a.severity,
 }));
 }, [alerts]);
 // Data source health: join internal sources + active external integrations.
 const sourcesHealth = useMemo(() => {
 const internal = (workspace?.internalSources || []).map((s) => ({
 id: s.id,
 name: s.name || SOURCE_TYPE_LABELS[s.sourceType] || "Source",
 type: SOURCE_TYPE_LABELS[s.sourceType] || s.sourceType,
 status: hash01(s.id) > 0.1 ? "healthy" : "degraded",
 latencyMs: 40 + Math.round(hash01(`${s.id}-lat`) * 260),
 lastSyncMins: 2 + Math.round(hash01(`${s.id}-sync`) * 28),
 kind: "internal",
 }));
 const external = (workspace?.externalIntegrations || [])
 .filter((e) => e.status === "active")
 .map((e) => ({
 id: e.id,
 name: e.name || e.providerId,
 type: e.providerId,
 status: e.connectionStatus === "failed" ? "degraded" : "healthy",
 latencyMs: 120 + Math.round(hash01(`${e.id}-lat`) * 340),
 lastSyncMins: 5 + Math.round(hash01(`${e.id}-sync`) * 45),
 kind: "external",
 }));
 return [...internal, ...external];
 }, [workspace]);
 if (!ready) {
 return (
 <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-8 py-6">
 <p className="text-base leading-6 text-text-secondary">Loading monitor…</p>
 </div>
 );
 }
 return (
 <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-8 py-6">
 <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
 <div className="min-w-0">
 <div className="flex items-center gap-3">
 <h1 className="text-2xl font-semibold tracking-tight text-text-primary">Monitor</h1>
 <TourLaunchButton tourId="monitor" label="Take a quick Monitor tour" />
 </div>
 <p className="mt-1 max-w-2xl text-base leading-6 text-text-secondary">
 Operational health across processes, use cases, and connected data. Anything that needs action shows up as an alert.
 </p>
 </div>
 <div className="flex items-center gap-2">
 <button
 type="button"
 onClick={() => navigate("/alerts")}
 className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white transition hover:bg-primary-hover"
 >
 <Bell className="h-4 w-4" aria-hidden />
 View all alerts
 {unreadCount > 0 ? (
 <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-status-critical-light px-1.5 text-[11px] font-semibold text-status-critical dark:bg-status-critical/20">
 {unreadCount}
 </span>
 ) : null}
 </button>
 </div>
 </div>
 {/* ── Header stat strip ────────────────────────────────────────────── */}
 <section className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" data-tour-monitor="stats">
 <StatTile
 icon={ClipboardList}
 iconTone="text-status-success bg-status-success-light dark:bg-status-success/15"
 label="Active use cases"
 value={stats.activeUseCases}
 helper={platformUseCases.length > stats.activeUseCases ? `${platformUseCases.length - stats.activeUseCases} paused or draft` : "On the platform"}
 onClick={() => navigate("/use-cases/gallery")}
 />
 <StatTile
 icon={Zap}
 iconTone="text-status-info bg-status-info-light dark:bg-status-info/15"
 label="Runs, 7 days"
 value={stats.runs7d.toLocaleString()}
 helper={stats.failed7d ? `${stats.failed7d} failed` : "None failed"}
 />
 <StatTile
 icon={Activity}
 iconTone={stats.silent > 0 ? "text-status-critical bg-status-critical-light dark:bg-status-critical/15" : "text-status-success bg-status-success-light dark:bg-status-success/15"}
 label="Records, 7 days"
 value={`${stats.created7d} · ${stats.exceptions7d}`}
 helper={stats.silent > 0 ? `${stats.silent} schedule(s) silent` : "created · held as exceptions"}
 />
 <StatTile
 icon={AlertTriangle}
 iconTone={
 stats.criticalAlerts > 0
 ? "text-status-critical bg-status-critical-light dark:bg-status-critical/15"
 : "text-status-success bg-status-success-light dark:bg-status-success/15"
 }
 label="Critical alerts"
 value={stats.criticalAlerts}
 helper={stats.criticalAlerts > 0 ? "Open and unresolved" : "Nothing critical"}
 onClick={() => navigate("/alerts?alert=")}
 />
 <StatTile
 icon={Database}
 iconTone="text-primary bg-primary-light dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30"
 label="Data sources"
 value={`${stats.sourcesOk} / ${stats.sourcesTotal}`}
 helper="Connected and healthy"
 onClick={() => navigate("/configure/internal-sources")}
 />
 </section>
 {/* ── Needs attention band ─────────────────────────────────────────── */}
 {topOpenAlerts.length > 0 ? (
 <section className="mb-6 rounded-xl border border-status-critical-border bg-status-critical-light p-4 dark:border-border dark:bg-gradient-to-b dark:from-status-critical/22 dark:to-surface-secondary dark:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)] dark:ring-1 dark:ring-status-critical-border/45" data-tour-monitor="attention">
 <header className="mb-3 flex items-center justify-between gap-3">
 <h2 className="flex items-center gap-2 text-sm font-semibold text-status-critical">
 <AlertTriangle className="h-4 w-4" aria-hidden />
 Needs attention
 {criticalUnreadCount > 0 ? (
 <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-status-critical-light px-1.5 text-[11px] font-semibold text-status-critical dark:bg-status-critical/20">
 {criticalUnreadCount}
 </span>
 ) : null}
 </h2>
 <Link
 to="/alerts"
 className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
 >
 Open inbox
 <ArrowRight className="h-3 w-3" aria-hidden />
 </Link>
 </header>
 <ul className="flex flex-col gap-2">
 {topOpenAlerts.map((a) => {
 const sev = SEVERITY_META[a.severity] || SEVERITY_META.info;
 return (
 <li key={a.id}>
 <Link
 to={`/alerts?alert=${encodeURIComponent(a.id)}`}
 className="flex items-start gap-3 rounded-lg bg-surface px-3 py-2 ring-1 ring-status-critical-border transition hover:ring-status-critical-ring dark:bg-surface-default dark:ring-status-critical-border/45"
 >
 <span className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${sev.dot}`} aria-hidden />
 <span className="min-w-0 flex-1">
 <span className="flex flex-wrap items-center gap-1.5">
 <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${sev.chipClass}`}>
 {sev.label}
 </span>
 {a.businessAreaId ? (
 <span className="inline-flex items-center rounded-full bg-primary-light px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30">
 {getBusinessAreaLabel(a.businessAreaId)}
 </span>
 ) : null}
 </span>
 <span className="mt-1 block truncate text-sm font-medium text-text-primary">{a.title}</span>
 <span className="mt-0.5 block text-xs text-text-muted">{relativeTimeFromNow(a.createdAt)}</span>
 </span>
 </Link>
 </li>
 );
 })}
 </ul>
 </section>
 ) : null}
 {/* ── Use case execution ───────────────────────────────────────────── */}
 <section className="mb-6" data-tour-monitor="use-cases">
 <header className="mb-3 flex items-center justify-between">
 <div>
 <h2 className="flex items-center gap-2 text-lg font-semibold text-text-primary">
 <ClipboardList className="h-5 w-5 text-status-success" aria-hidden />
 Use case execution
 </h2>
 <p className="mt-0.5 text-sm text-text-secondary">
 Every use case on the platform: its schedule, what fires it, its last run, and what the last seven days created and held.
 </p>
 </div>
 <Link
 to="/use-cases/gallery"
 className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
 >
 Manage use cases
 <ArrowRight className="h-3 w-3" aria-hidden />
 </Link>
 </header>
 {healthError && <p className="mb-3 rounded-lg border border-status-critical-border bg-status-critical-light px-3 py-2 text-sm text-status-critical">{healthError}</p>}
 {health === null && !healthError && <p className="text-sm text-text-muted">Reading the platform…</p>}
 {health !== null && platformUseCases.length === 0 && !healthError && (
 <p className="rounded-xl border border-dashed border-border bg-surface-secondary px-6 py-10 text-center text-sm text-text-secondary">No use case has been activated on the platform yet.</p>
 )}
 {platformUseCases.length > 0 && (
 <div className="overflow-x-auto rounded-xl border border-border bg-surface shadow-sm dark:bg-surface-default">
 <table className="w-full min-w-[820px] text-sm">
 <thead className="bg-surface-secondary text-[11px] uppercase tracking-wide text-text-muted">
 <tr><th className="px-3 py-2 text-left">Use case</th><th className="px-3 py-2 text-left">Schedule</th><th className="px-3 py-2 text-left">Last run</th><th className="px-3 py-2 text-left">Next expected</th><th className="px-3 py-2 text-right">Runs 7d</th><th className="px-3 py-2 text-right">Created 7d</th><th className="px-3 py-2 text-right">Exceptions 7d</th><th className="px-3 py-2 text-left">Health</th></tr>
 </thead>
 <tbody>
 {platformUseCases.map((u) => {
 const tone = u.status !== "activated" ? "muted" : u.silent || u.last_run_status === "failed" ? "critical" : u.failures_7d > 0 ? "warning" : "ok";
 const label = u.status === "paused" ? "Paused" : u.status !== "activated" ? "Not active" : u.silent ? "Silent" : u.last_run_status === "failed" ? "Last run failed" : u.failures_7d > 0 ? "Degraded" : u.last_run_status ? "Healthy" : "Waiting for first run";
 const next = u.next_expected_at ? new Date(u.next_expected_at) : null;
 const due = next && next.getTime() < Date.now();
 return (
 <tr key={u.use_case_id} className="cursor-pointer border-t border-border hover:bg-surface-secondary/60" onClick={() => navigate(`/use-cases/platform/${encodeURIComponent(u.use_case_id)}`)}>
 <td className="px-3 py-2"><p className="font-semibold text-text-primary">{u.title}</p><p className="text-[11px] text-text-muted">v{u.version} · {u.status}</p></td>
 <td className="px-3 py-2 text-xs text-text-secondary">{u.schedule ? cadenceLabel(u.schedule) : "on demand"}{u.schedule && <p className="text-[11px] text-text-muted">{u.trigger === "poller" ? "fired by the local poller" : u.trigger === "orchestrator" ? "fired by the orchestrator" : "nothing fires it here; Run now only"}</p>}</td>
 <td className="px-3 py-2 text-xs text-text-secondary">{u.last_run_at ? <><span className={u.last_run_status === "succeeded" ? "text-status-success" : u.last_run_status === "failed" ? "text-status-critical" : ""}>{u.last_run_status}</span> · {relativeTimeFromNow(new Date(u.last_run_at).getTime())}<p className="text-[11px] text-text-muted">{u.last_run_summary}</p></> : "never"}</td>
 <td className="px-3 py-2 text-xs text-text-secondary">{next ? <span className={due ? "text-status-critical" : ""}>{due ? "overdue since " : ""}{next.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span> : "—"}</td>
 <td className="px-3 py-2 text-right tabular-nums text-text-primary">{u.runs_7d}{u.failures_7d ? <span className="ml-1 text-[11px] text-status-critical">({u.failures_7d} failed)</span> : null}</td>
 <td className="px-3 py-2 text-right tabular-nums text-status-success">{u.created_7d}</td>
 <td className="px-3 py-2 text-right tabular-nums text-status-critical">{u.exceptions_7d}</td>
 <td className="px-3 py-2"><StatusPill tone={tone} label={label} /></td>
 </tr>
 );
 })}
 </tbody>
 </table>
 {health?.as_of && <p className="px-3 py-2 text-[11px] text-text-muted">As of {new Date(health.as_of).toLocaleTimeString()} · refreshes every 30 s · a schedule is silent when one and a half cadences pass with nothing fired.</p>}
 </div>
 )}
 </section>
 {/* ── Two-column: KPI drift + Activity feed ───────────────────────── */}
 <section className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-[2fr_3fr]" data-tour-monitor="drift-activity">
 <div className="rounded-xl border border-border bg-surface p-4 shadow-sm dark:bg-surface-default">
 <header className="mb-3 flex items-center justify-between">
 <h2 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
 <LineChart className="h-4 w-4 text-primary" aria-hidden />
 KPI drift
 </h2>
 <Link
 to="/kpis/registry"
 className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
 >
 Registry
 <ArrowRight className="h-3 w-3" aria-hidden />
 </Link>
 </header>
 <ul className="flex flex-col divide-y divide-border">
 {kpiDrift.length === 0 ? (
 <li className="py-6 text-center text-xs text-text-muted">No drift detected.</li>
 ) : (
 kpiDrift.map((k) => (
 <li key={k.id} className="flex items-center justify-between gap-2 py-2.5">
 <div className="min-w-0">
 <p className="truncate text-sm font-medium text-text-primary">{k.name}</p>
 <p className="mt-0.5 text-xs text-text-muted">
 {getBusinessAreaLabel(k.businessAreaId) || "-"}
 </p>
 </div>
 <div className="flex shrink-0 items-baseline gap-2">
 <span className="text-sm font-semibold text-text-primary">{k.value}</span>
 {k.delta ? (
 <span
 className={`inline-flex items-center gap-0.5 text-xs font-semibold ${
 k.severity === "info" ? "text-status-success" : "text-status-critical"
 }`}
 >
 {k.severity === "info" ? (
 <ArrowUpRight className="h-3 w-3" aria-hidden />
 ) : (
 <ArrowDownRight className="h-3 w-3" aria-hidden />
 )}
 {k.delta}
 </span>
 ) : null}
 </div>
 </li>
 ))
 )}
 </ul>
 </div>
 <div className="rounded-xl border border-border bg-surface p-4 shadow-sm dark:bg-surface-default">
 <header className="mb-3 flex items-center justify-between">
 <h2 className="flex items-center gap-2 text-sm font-semibold text-text-primary">
 <Activity className="h-4 w-4 text-primary" aria-hidden />
 Recent activity
 </h2>
 <span className="text-xs text-text-muted">Last 24h</span>
 </header>
 <ol className="flex flex-col gap-0.5">
 {activityFeed.map((item) => (
 <li key={item.id} className="flex items-start gap-3 rounded-md px-2 py-1.5 hover:bg-surface-secondary">
 <span
 className={`mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
 item.kind === "alert"
 ? item.severity === "critical"
 ? "bg-status-critical-dot"
 : item.severity === "warning"
 ? "bg-status-warning-dot"
 : "bg-status-info-dot"
 : item.kind === "use-case"
 ? "bg-status-success-dot"
 : "bg-text-muted"
 }`}
 aria-hidden
 />
 <span className="min-w-0 flex-1">
 <span className="block truncate text-sm text-text-primary">{item.title}</span>
 <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-text-muted">
 <span>{relativeTimeFromNow(item.at)}</span>
 {item.processName ? (
 <>
 <span>·</span>
 <span className="inline-flex items-center gap-1">
 <GitBranch className="h-3 w-3" aria-hidden />
 {item.processName}
 </span>
 </>
 ) : null}
 {item.tag ? (
 <>
 <span>·</span>
 <span>{item.tag}</span>
 </>
 ) : null}
 </span>
 </span>
 </li>
 ))}
 </ol>
 </div>
 </section>
 {/* ── Data source health ──────────────────────────────────────────── */}
 <section data-tour-monitor="sources">
 <header className="mb-3 flex items-center justify-between">
 <div>
 <h2 className="flex items-center gap-2 text-lg font-semibold text-text-primary">
 <Database className="h-5 w-5 text-primary" aria-hidden />
 Data source health
 </h2>
 <p className="mt-0.5 text-sm text-text-secondary">
 Upstream systems the agent reads from.
 </p>
 </div>
 <Link
 to="/configure/internal-sources"
 className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
 >
 Manage sources
 <ArrowRight className="h-3 w-3" aria-hidden />
 </Link>
 </header>
 {sourcesHealth.length === 0 ? (
 <p className="rounded-xl border border-dashed border-border bg-surface-secondary px-6 py-10 text-center text-sm text-text-secondary">
 No data sources configured yet. Connect one from Configure to see health here.
 </p>
 ) : (
 <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
 {sourcesHealth.map((s) => (
 <article
 key={s.id}
 className="flex min-w-0 items-start gap-3 rounded-xl border border-border bg-surface p-4 shadow-sm dark:bg-surface-default"
 >
 <div
 className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
 s.status === "healthy"
 ? "bg-status-success-light text-status-success dark:bg-surface-tertiary dark:text-status-success dark:ring-1 dark:ring-status-success-border/45"
 : "bg-status-critical-light text-status-critical dark:bg-surface-tertiary dark:text-status-critical dark:ring-1 dark:ring-status-critical-border/45"
 }`}
 >
 {s.status === "healthy" ? (
 <CheckCircle2 className="h-4 w-4" aria-hidden />
 ) : (
 <AlertTriangle className="h-4 w-4" aria-hidden />
 )}
 </div>
 <div className="min-w-0 flex-1">
 <h3 className="truncate text-sm font-semibold text-text-primary">{s.name}</h3>
 <p className="mt-0.5 text-xs text-text-muted">
 {s.type}
 <span className="mx-1">·</span>
 {s.kind === "internal" ? "Internal" : "Integration"}
 </p>
 <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-text-secondary">
 <span>
 <span className="font-semibold text-text-primary">{s.latencyMs}</span> ms
 </span>
 <span>
 Last sync <span className="font-semibold text-text-primary">{s.lastSyncMins}</span> min ago
 </span>
 </div>
 </div>
 <StatusPill tone={s.status === "healthy" ? "ok" : "warning"} label={s.status === "healthy" ? "Healthy" : "Degraded"} />
 </article>
 ))}
 </div>
 )}
 </section>
 </div>
 );
}
// ── Helpers ──────────────────────────────────────────────────────────────
function StatTile({ icon: Icon, iconTone, label, value, helper, onClick }) {
 const clickable = typeof onClick === "function";
 const Wrapper = clickable ? "button" : "div";
 return (
 <Wrapper
 type={clickable ? "button" : undefined}
 onClick={clickable ? onClick : undefined}
 className={`flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-surface p-4 text-left shadow-sm dark:bg-surface-default ${
 clickable ? "transition hover:border-border-light" : ""
 }`}
 >
 <div className="flex items-center gap-2">
 <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${iconTone}`}>
 <Icon className="h-4 w-4" aria-hidden />
 </span>
 <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">{label}</span>
 </div>
 <div className="text-2xl font-semibold leading-tight text-text-primary">{value}</div>
 {helper ? <p className="text-xs text-text-muted">{helper}</p> : null}
 </Wrapper>
 );
}
function StatusPill({ tone, label }) {
 const toneClass =
 tone === "ok"
 ? "bg-status-success-light text-status-success ring-1 ring-status-success-border dark:bg-status-success/15 dark:ring-status-success-border/55"
 : tone === "warning"
 ? "bg-status-warning-light text-status-warning ring-1 ring-status-warning-border dark:bg-status-warning/15 dark:ring-status-warning-border/55"
 : tone === "muted"
 ? "bg-surface-secondary text-text-muted ring-1 ring-border"
 : "bg-status-critical-light text-status-critical ring-1 ring-status-critical-border dark:bg-status-critical/15 dark:ring-status-critical-border/55";
 return (
 <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${toneClass}`}>
 <span
 className={`inline-block h-1.5 w-1.5 rounded-full ${
 tone === "ok" ? "bg-status-success-dot" : tone === "warning" ? "bg-status-warning-dot" : tone === "muted" ? "bg-border-light" : "bg-status-critical-dot"
 }`}
 aria-hidden
 />
 {label}
 </span>
 );
}
