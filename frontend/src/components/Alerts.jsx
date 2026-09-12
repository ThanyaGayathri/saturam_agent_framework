// Alerts inbox. Full triage view backed by the shared alerts store: the
// same data also powers the bell dropdown in the top bar and (later) the
// Monitor page. Supports deep-linking a specific alert via `?alert=<id>`.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Check,
  ClipboardList,
  Clock,
  GitBranch,
  Info,
  MessageSquareText,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import { useAlerts } from "../hooks/useAlerts";
import {
  SEVERITY_META,
  SNOOZE_OPTIONS,
  STATUS_META,
  getBusinessAreaLabel,
  relativeTimeFromNow,
  sortAlertsForDisplay,
  alertKpiDeltaTextClass,
} from "../domain/alerts-view";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { useProcesses } from "../hooks/useProcesses";
import { PLAYGROUND_HUB_CHAT_MESSAGE_KEY } from "../storage/keys";
import TourLaunchButton from "../tours/TourLaunchButton";
const STATUS_FILTERS = [
  { id: "open", label: "Open", match: (a) => a.status !== "resolved" },
  { id: "new", label: "New", match: (a) => a.status === "new" },
  { id: "acknowledged", label: "Acknowledged", match: (a) => a.status === "acknowledged" },
  { id: "snoozed", label: "Snoozed", match: (a) => a.status === "snoozed" },
  { id: "resolved", label: "Resolved", match: (a) => a.status === "resolved" },
  { id: "all", label: "All", match: () => true },
];
const SEVERITY_FILTERS = ["critical", "warning", "info"];
const SEVERITY_ICON = {
  critical: AlertTriangle,
  warning: AlertCircle,
  info: Info,
};
export default function Alerts() {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    alerts,
    hydrated,
    unreadCount,
    acknowledge,
    snooze,
    resolve,
    reopen,
    markAllRead,
  } = useAlerts();
  const { processes } = useProcesses();
  const [statusFilter, setStatusFilter] = useState("open");
  const [severityFilter, setSeverityFilter] = useState(null);
  const [areaFilter, setAreaFilter] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const processById = useMemo(
    () => Object.fromEntries((processes || []).map((p) => [p.id, p])),
    [processes]
  );
  // Pick up `?alert=<id>` from the URL (bell dropdown → alert detail).
  const readInitialIdRef = useRef(false);
  useEffect(() => {
    if (readInitialIdRef.current) return;
    readInitialIdRef.current = true;
    const params = new URLSearchParams(location.search);
    const id = params.get("alert");
    if (id) setSelectedId(id);
  }, [location.search]);
  const filtered = useMemo(() => {
    const statusCfg = STATUS_FILTERS.find((s) => s.id === statusFilter);
    let out = alerts.filter((a) => (statusCfg ? statusCfg.match(a) : true));
    if (severityFilter) out = out.filter((a) => a.severity === severityFilter);
    if (areaFilter) out = out.filter((a) => a.businessAreaId === areaFilter);
    return sortAlertsForDisplay(out);
  }, [alerts, statusFilter, severityFilter, areaFilter]);
  const selected = useMemo(
    () => alerts.find((a) => a.id === selectedId) || null,
    [alerts, selectedId]
  );
  const countsByStatus = useMemo(() => {
    const counts = {};
    for (const s of STATUS_FILTERS) counts[s.id] = 0;
    for (const a of alerts) {
      for (const s of STATUS_FILTERS) {
        if (s.match(a)) counts[s.id] += 1;
      }
    }
    return counts;
  }, [alerts]);
  const openSelected = useCallback(
    (id) => {
      setSelectedId(id);
      const params = new URLSearchParams(location.search);
      params.set("alert", id);
      navigate({ pathname: location.pathname, search: `?${params.toString()}` }, { replace: true });
    },
    [location.pathname, location.search, navigate]
  );
  const closeSelected = useCallback(() => {
    setSelectedId(null);
    const params = new URLSearchParams(location.search);
    params.delete("alert");
    const search = params.toString();
    navigate(
      { pathname: location.pathname, search: search ? `?${search}` : "" },
      { replace: true }
    );
  }, [location.pathname, location.search, navigate]);
  useEffect(() => {
    if (!selectedId) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") closeSelected();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, closeSelected]);
  const investigateInChat = useCallback(
    (alert) => {
      if (!alert) return;
      const prompt = `Investigate this alert: "${alert.title}". Summary: ${alert.summary} Rule: ${alert.rule}. What should I check first?`;
      try {
        window.sessionStorage.setItem(PLAYGROUND_HUB_CHAT_MESSAGE_KEY, prompt);
      } catch {
        /* ignore */
      }
      navigate("/playground/chat");
    },
    [navigate]
  );
  if (!hydrated) {
    return (
      <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-8 py-6">
        <p className="text-base leading-6 text-text-secondary">Loading alerts…</p>
      </div>
    );
  }
  return (
    <div className="mx-auto min-h-[calc(100vh-5rem)] w-full max-w-[1440px] px-8 py-6">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">Alerts</h1>
            <TourLaunchButton tourId="alerts" label="Take a quick Alerts tour" />
          </div>
          <p className="mt-1 max-w-2xl text-base leading-6 text-text-secondary">
            Things that need attention across your processes and KPIs. Each alert links back to the business area it came from.
          </p>
        </div>
        {unreadCount > 0 ? (
          <button
            type="button"
            onClick={markAllRead}
            className="inline-flex items-center gap-2 self-start rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white transition hover:bg-primary-hover"
          >
            <Check className="h-4 w-4" aria-hidden />
            Mark all read ({unreadCount})
          </button>
        ) : null}
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[240px_minmax(0,1fr)]" data-tour-alerts="body">
        <div data-tour-alerts="rail">
        <FilterRail
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
          countsByStatus={countsByStatus}
          severityFilter={severityFilter}
          setSeverityFilter={setSeverityFilter}
          areaFilter={areaFilter}
          setAreaFilter={setAreaFilter}
          alerts={alerts}
        />
        </div>
        <div className="min-w-0" data-tour-alerts="list">
          <div className="mb-3 flex items-center justify-between text-xs text-text-muted">
            <span>
              Showing <span className="font-semibold text-text-primary">{filtered.length}</span> of {alerts.length} alerts
            </span>
            {severityFilter || areaFilter ? (
              <button
                type="button"
                onClick={() => {
                  setSeverityFilter(null);
                  setAreaFilter(null);
                }}
                className="font-semibold text-primary hover:text-primary-hover"
              >
                Clear filters
              </button>
            ) : null}
          </div>
          <ul className="flex flex-col gap-3">
            {filtered.length === 0 ? (
              <li className="rounded-xl border border-dashed border-border bg-surface-secondary px-6 py-12 text-center text-sm text-text-secondary">
                No alerts match these filters.
              </li>
            ) : (
              filtered.map((a) => (
                <AlertRow
                  key={a.id}
                  alert={a}
                  process={processById[a.processId]}
                  onOpen={() => openSelected(a.id)}
                  onAcknowledge={() => acknowledge(a.id)}
                  onSnooze={(ms) => snooze(a.id, Date.now() + ms)}
                  onResolve={() => resolve(a.id)}
                  onReopen={() => reopen(a.id)}
                  selected={a.id === selectedId}
                />
              ))
            )}
          </ul>
        </div>
      </div>
      {selected ? (
        <DetailDrawer
          alert={selected}
          process={processById[selected.processId]}
          onClose={closeSelected}
          onAcknowledge={() => acknowledge(selected.id)}
          onSnooze={(ms) => snooze(selected.id, Date.now() + ms)}
          onResolve={() => resolve(selected.id)}
          onReopen={() => reopen(selected.id)}
          onInvestigate={() => investigateInChat(selected)}
        />
      ) : null}
    </div>
  );
}
// ── Left filter rail ──────────────────────────────────────────────────────
function FilterRail({
  statusFilter,
  setStatusFilter,
  countsByStatus,
  severityFilter,
  setSeverityFilter,
  areaFilter,
  setAreaFilter,
  alerts,
}) {
  const severityCounts = useMemo(() => {
    const c = { critical: 0, warning: 0, info: 0 };
    for (const a of alerts) {
      if (a.status === "resolved") continue;
      if (c[a.severity] != null) c[a.severity] += 1;
    }
    return c;
  }, [alerts]);
  const { areas: businessAreas } = useBusinessAreas();
  const areasWithCounts = useMemo(() => {
    const counts = new Map();
    for (const a of alerts) {
      if (a.status === "resolved") continue;
      if (!a.businessAreaId) continue;
      counts.set(a.businessAreaId, (counts.get(a.businessAreaId) || 0) + 1);
    }
    return businessAreas.filter((b) => counts.has(b.id)).map((b) => ({
      ...b,
      count: counts.get(b.id) || 0,
    }));
  }, [alerts, businessAreas]);
  return (
    <aside className="flex flex-col gap-5 lg:sticky lg:top-[88px] lg:h-fit">
      <div>
        <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
          Status
        </h2>
        <ul className="flex flex-col gap-1">
          {STATUS_FILTERS.map((s) => {
            const active = s.id === statusFilter;
            return (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => setStatusFilter(s.id)}
                  className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors ${
                    active
                      ? "bg-primary-light text-primary font-semibold dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30"
                      : "text-text-tertiary hover:bg-surface-secondary"
                  }`}
                >
                  <span>{s.label}</span>
                  <span className="text-xs text-text-muted">{countsByStatus[s.id] ?? 0}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
      <div>
        <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
          Severity
        </h2>
        <ul className="flex flex-col gap-1">
          {SEVERITY_FILTERS.map((s) => {
            const active = severityFilter === s;
            const meta = SEVERITY_META[s];
            return (
              <li key={s}>
                <button
                  type="button"
                  onClick={() => setSeverityFilter(active ? null : s)}
                  className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors ${
                    active
                      ? "bg-primary-light text-primary font-semibold dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30"
                      : "text-text-tertiary hover:bg-surface-secondary"
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${meta.dot}`} aria-hidden />
                    <span className={active ? "" : meta.railHeadingCls}>{meta.label}</span>
                  </span>
                  <span className="text-xs text-text-muted">{severityCounts[s] || 0}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
      {areasWithCounts.length > 0 ? (
        <div>
          <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
            Business area
          </h2>
          <ul className="flex flex-col gap-1">
            {areasWithCounts.map((b) => {
              const active = areaFilter === b.id;
              return (
                <li key={b.id}>
                  <button
                    type="button"
                    onClick={() => setAreaFilter(active ? null : b.id)}
                    className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors ${
                      active
                        ? "bg-primary-light text-primary font-semibold dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30"
                        : "text-text-tertiary hover:bg-surface-secondary"
                    }`}
                  >
                    <span>{b.label}</span>
                    <span className="text-xs text-text-muted">{b.count}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </aside>
  );
}
// ── Alert row ─────────────────────────────────────────────────────────────
function AlertRow({
  alert,
  process,
  onOpen,
  onAcknowledge,
  onSnooze,
  onResolve,
  onReopen,
  selected,
}) {
  const sev = SEVERITY_META[alert.severity] || SEVERITY_META.info;
  const stat = STATUS_META[alert.status] || STATUS_META.new;
  const Icon = SEVERITY_ICON[alert.severity] || Info;
  const isResolved = alert.status === "resolved";
  return (
    <li
      className={`overflow-hidden rounded-xl bg-surface shadow-sm transition dark:bg-surface-default ${
        selected ? "ring-2 ring-primary" : "ring-1 ring-border"
      } ${sev.rowAccent} ${isResolved ? "opacity-70" : ""}`}
    >
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:gap-4">
        <button
          type="button"
          onClick={onOpen}
          className="flex min-w-0 flex-1 items-start gap-3 text-left"
        >
          <Icon
            className={`mt-0.5 h-5 w-5 shrink-0 ${sev.iconCls}`}
            strokeWidth={2}
            aria-hidden
          />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${sev.chipClass}`}
              >
                {sev.label}
              </span>
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${stat.chipClass}`}
              >
                {stat.label}
              </span>
              {alert.businessAreaId ? (
                <span className="inline-flex items-center rounded-full bg-primary-light px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30">
                  {getBusinessAreaLabel(alert.businessAreaId)}
                </span>
              ) : null}
              {process ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-status-info-border bg-status-info-light px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-info dark:border-status-info-border/50 dark:bg-status-info/12 dark:text-status-info">
                  <GitBranch className="h-3 w-3" aria-hidden />
                  {process.name}
                </span>
              ) : null}
            </div>
            <p className="mt-1.5 text-[15px] font-semibold text-text-primary">{alert.title}</p>
            {alert.kpi ? (
              <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-secondary">
                <BarChart3 className="h-3.5 w-3.5 text-primary" aria-hidden />
                <span>
                  {alert.kpi.name}:{" "}
                  <span className="font-semibold text-text-primary">{alert.kpi.value}</span>
                </span>
                {alert.kpi.delta ? (
                  <span className={`font-semibold ${alertKpiDeltaTextClass(alert)}`}>
                    ({alert.kpi.delta})
                  </span>
                ) : null}
              </p>
            ) : null}
            <p className="mt-1 flex items-center gap-1 text-xs text-text-muted">
              <Clock className="h-3 w-3" aria-hidden />
              {relativeTimeFromNow(alert.createdAt)}
              {alert.status === "snoozed" && alert.snoozeUntil ? (
                <span className="ml-2">
                  Snoozed until {new Date(alert.snoozeUntil).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                </span>
              ) : null}
            </p>
          </div>
        </button>
        <RowActions
          alert={alert}
          onAcknowledge={onAcknowledge}
          onSnooze={onSnooze}
          onResolve={onResolve}
          onReopen={onReopen}
        />
      </div>
    </li>
  );
}
function RowActions({ alert, onAcknowledge, onSnooze, onResolve, onReopen }) {
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const wrapperRef = useRef(null);
  useEffect(() => {
    if (!snoozeOpen) return undefined;
    const onClick = (e) => {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target)) setSnoozeOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [snoozeOpen]);
  if (alert.status === "resolved") {
    return (
      <button
        type="button"
        onClick={onReopen}
        className="inline-flex shrink-0 items-center gap-1 self-start rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs font-semibold text-text-tertiary hover:border-border-light"
      >
        <RotateCcw className="h-3.5 w-3.5" aria-hidden />
        Reopen
      </button>
    );
  }
  return (
    <div ref={wrapperRef} className="relative flex shrink-0 flex-wrap items-start gap-1 self-start">
      {alert.status === "new" ? (
        <button
          type="button"
          onClick={onAcknowledge}
          className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs font-semibold text-text-tertiary hover:border-border-light"
          title="Acknowledge"
        >
          <Check className="h-3.5 w-3.5" aria-hidden />
          Ack
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => setSnoozeOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs font-semibold text-text-tertiary hover:border-border-light"
        title="Snooze"
      >
        <Clock className="h-3.5 w-3.5" aria-hidden />
        Snooze
      </button>
      <button
        type="button"
        onClick={onResolve}
        className="inline-flex items-center gap-1 rounded-lg border border-[#12b76a] bg-[#ecfdf3] px-2.5 py-1.5 text-xs font-semibold text-[#067647] hover:bg-[#d1fadf]"
        title="Resolve"
      >
        <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
        Resolve
      </button>
      {snoozeOpen ? (
        <div className="absolute right-0 top-full z-20 mt-1 w-44 overflow-hidden rounded-lg border border-border bg-surface shadow-lg">
          {SNOOZE_OPTIONS.map((opt) => (
            <button
              key={opt.label}
              type="button"
              onClick={() => {
                onSnooze(opt.ms);
                setSnoozeOpen(false);
              }}
              className="block w-full px-3 py-2 text-left text-xs text-text-tertiary hover:bg-surface-secondary"
            >
              {opt.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
// ── Detail drawer ─────────────────────────────────────────────────────────
function DetailDrawer({
  alert,
  process,
  onClose,
  onAcknowledge,
  onSnooze,
  onResolve,
  onReopen,
  onInvestigate,
}) {
  const sev = SEVERITY_META[alert.severity] || SEVERITY_META.info;
  const stat = STATUS_META[alert.status] || STATUS_META.new;
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  return (
    <div className="fixed inset-0 z-30" role="dialog" aria-modal="true" aria-label="Alert details">
      <button
        type="button"
        onClick={onClose}
        className="absolute inset-0 bg-black/40"
        aria-label="Close alert details"
      />
      <aside className="absolute right-0 top-[72px] flex h-[calc(100vh-72px)] w-full max-w-md flex-col overflow-y-auto bg-surface shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${sev.chipClass}`}
              >
                {sev.label}
              </span>
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${stat.chipClass}`}
              >
                {stat.label}
              </span>
            </div>
            <h2 className="mt-2 text-lg font-semibold leading-snug text-text-primary">{alert.title}</h2>
            <p className="mt-1 flex items-center gap-1 text-xs text-text-muted">
              <Clock className="h-3 w-3" aria-hidden />
              {relativeTimeFromNow(alert.createdAt)}
              <span className="mx-1">·</span>
              {new Date(alert.createdAt).toLocaleString()}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-secondary hover:bg-surface-secondary"
            aria-label="Close"
          >
            <X className="h-5 w-5" aria-hidden />
          </button>
        </header>
        <div className="flex flex-col gap-5 p-5">
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Summary
            </h3>
            <p className="mt-1.5 text-sm leading-relaxed text-text-tertiary">{alert.summary}</p>
          </section>
          {alert.kpi ? (
            <section className="rounded-lg border border-border bg-surface-secondary p-4">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-primary">
                <BarChart3 className="h-3.5 w-3.5" aria-hidden />
                Affected KPI
              </h3>
              <p className="mt-1.5 text-sm font-semibold text-text-primary">{alert.kpi.name}</p>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-2xl font-semibold text-text-primary">{alert.kpi.value}</span>
                {alert.kpi.delta ? (
                  <span className={`text-sm font-semibold ${alertKpiDeltaTextClass(alert)}`}>
                    {alert.kpi.delta}
                  </span>
                ) : null}
              </div>
              {alert.rule ? (
                <p className="mt-2 text-xs text-text-muted">
                  <span className="font-semibold text-text-tertiary">Rule: </span>
                  {alert.rule}
                </p>
              ) : null}
            </section>
          ) : null}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Context
            </h3>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {alert.businessAreaId ? (
                <Link
                  to={`/business-areas`}
                  className="inline-flex items-center gap-1 rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-primary transition hover:bg-primary-light-hover dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30 dark:hover:bg-surface-tertiary/90"
                >
                  {getBusinessAreaLabel(alert.businessAreaId)}
                </Link>
              ) : null}
              {process ? (
                <Link
                  to={`/processes#area-${alert.businessAreaId}`}
                  className="inline-flex items-center gap-1 rounded-full border border-status-info-border bg-status-info-light px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-status-info transition hover:bg-surface-secondary dark:border-status-info-border/50 dark:bg-status-info/12 dark:hover:bg-status-info/18"
                >
                  <GitBranch className="h-3 w-3" aria-hidden />
                  {process.name}
                </Link>
              ) : null}
              {alert.useCaseId ? (
                <Link
                  to="/use-cases/gallery"
                  className="inline-flex items-center gap-1 rounded-full bg-status-success-light px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-status-success transition hover:bg-status-success-muted dark:bg-status-success/15 dark:hover:bg-status-success/22"
                >
                  <ClipboardList className="h-3 w-3" aria-hidden />
                  Linked use case
                </Link>
              ) : null}
            </div>
          </section>
          {alert.recommendedActions && alert.recommendedActions.length > 0 ? (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                Recommended next steps
              </h3>
              <ul className="mt-2 flex flex-col gap-1.5">
                {alert.recommendedActions.map((action, i) => (
                  <li
                    key={i}
                    className="flex gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-tertiary"
                  >
                    <span className="mt-0.5 text-primary">·</span>
                    <span>{action}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Timeline
            </h3>
            <ol className="mt-2 flex flex-col gap-2 text-xs">
              <li className="flex items-center gap-2 text-text-tertiary">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#d92d20]" aria-hidden />
                Fired {relativeTimeFromNow(alert.createdAt)}
              </li>
              {alert.acknowledgedAt ? (
                <li className="flex items-center gap-2 text-text-tertiary">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#dc6803]" aria-hidden />
                  Acknowledged {relativeTimeFromNow(alert.acknowledgedAt)}
                </li>
              ) : null}
              {alert.snoozeUntil ? (
                <li className="flex items-center gap-2 text-text-tertiary">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#98a2b3]" aria-hidden />
                  Snoozed until {new Date(alert.snoozeUntil).toLocaleString()}
                </li>
              ) : null}
              {alert.resolvedAt ? (
                <li className="flex items-center gap-2 text-text-tertiary">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#12b76a]" aria-hidden />
                  Resolved {relativeTimeFromNow(alert.resolvedAt)}
                </li>
              ) : null}
            </ol>
          </section>
        </div>
        <footer className="mt-auto flex flex-col gap-2 border-t border-border bg-surface-secondary p-4">
          <button
            type="button"
            onClick={onInvestigate}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white transition hover:bg-[#53389e]"
          >
            <MessageSquareText className="h-4 w-4" aria-hidden />
            Investigate in Playground
            <ArrowRight className="h-4 w-4" aria-hidden />
          </button>
          <div className="flex flex-wrap items-center gap-2">
            {alert.status === "resolved" ? (
              <button
                type="button"
                onClick={onReopen}
                className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-tertiary hover:border-border-light"
              >
                <RotateCcw className="h-4 w-4" aria-hidden />
                Reopen
              </button>
            ) : (
              <>
                {alert.status === "new" ? (
                  <button
                    type="button"
                    onClick={onAcknowledge}
                    className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-tertiary hover:border-border-light"
                  >
                    <Check className="h-4 w-4" aria-hidden />
                    Acknowledge
                  </button>
                ) : null}
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setSnoozeOpen((v) => !v)}
                    className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-tertiary hover:border-border-light"
                  >
                    <Clock className="h-4 w-4" aria-hidden />
                    Snooze
                  </button>
                  {snoozeOpen ? (
                    <div className="absolute bottom-full left-0 z-10 mb-1 w-44 overflow-hidden rounded-lg border border-border bg-surface shadow-lg">
                      {SNOOZE_OPTIONS.map((opt) => (
                        <button
                          key={opt.label}
                          type="button"
                          onClick={() => {
                            onSnooze(opt.ms);
                            setSnoozeOpen(false);
                          }}
                          className="block w-full px-3 py-2 text-left text-xs text-text-tertiary hover:bg-surface-secondary"
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={onResolve}
                  className="inline-flex items-center gap-1 rounded-lg border border-[#12b76a] bg-[#ecfdf3] px-3 py-2 text-sm font-semibold text-[#067647] hover:bg-[#d1fadf]"
                >
                  <ShieldCheck className="h-4 w-4" aria-hidden />
                  Resolve
                </button>
              </>
            )}
          </div>
        </footer>
      </aside>
    </div>
  );
}
