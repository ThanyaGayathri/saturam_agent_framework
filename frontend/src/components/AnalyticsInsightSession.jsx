import { useCallback, useEffect, useMemo, useState } from "react";

import { useLocation, useNavigate, useParams, Link } from "react-router-dom";

import { BarChart3, Copy, ExternalLink, LayoutDashboard, MessageSquare, Zap } from "lucide-react";

import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";

import { useDemoProfile } from "../context/DemoProfileContext";

import Breadcrumb from "./shared/Breadcrumb";


import InsightSessionChartPanel from "./middleby/InsightSessionChartPanel";

import InsightSessionAutomationPanel from "./middleby/InsightSessionAutomationPanel";

import InsightSessionMonitorPanel from "./middleby/InsightSessionMonitorPanel";

import InsightSessionToolbar from "./middleby/InsightSessionToolbar";

// MiddlebyDataHonestyFooter removed - its "How this dataset was built" content
// is now covered by the "Why you're seeing this" and "What Middleby needs to send"
// panels in InsightSessionToolbar, which are visible above every tab.

import MiddlebyBrandDrawer from "./middleby/MiddlebyBrandDrawer";

import MiddlebySkuSheet from "./middleby/MiddlebySkuSheet";

import { enrichCatalogEntry } from "../domain/middleby-catalog-helpers";

import { useReport } from "../hooks/useReport";

import { useChatFilters } from "../hooks/useChatFilters";
import PeriodFilterChip from "./middleby/PeriodFilterChip";

import {

  buildKpiChips,

  buildRecommendedActions,

  getDrillSpecs,

  getMiddlebyPrimarySpec,

  getSpecByChartRole,

  loadMiddlebyDataset,

  scaleMiddlebyRowsForPeriod,

} from "../domain/middleby-analytics-data";

import { resolveMiddlebyPeriodSpan } from "../domain/middleby-dataset-info";

import { resolveMiddlebyInsightHeadline } from "../domain/middleby-insight-headline";

import { findMiddlebyBrand, findMiddlebyProduct } from "../domain/middleby-shop-catalog";

import { logMiddlebyTelemetry } from "../domain/middleby-demo-telemetry";

import { normalizeDemoRoleId } from "../domain/demo-profiles";

import { getMonitorForCatalogId, monitorPath } from "../domain/middleby-monitor-manifest";
import { getMiddlebyTrackOverTime } from "../domain/middleby-track-over-time";

import { getPatternsForCatalogId } from "../domain/automation-registry";

import {
  PORTFOLIO_EXECUTIVE_CATALOG_IDS,
  PORTFOLIO_PERFORMANCE_PATH,
} from "../domain/middleby-financial-demo-data";



const SESSION_VIEWS = [

  { id: "insight", label: "The answer", icon: MessageSquare },

  { id: "chart", label: "See the data", icon: BarChart3 },

  { id: "monitor", label: "Track over time", icon: LayoutDashboard },

  { id: "automate", label: "Run automatically", icon: Zap },

];

const SESSION_VIEW_HINTS = {

  chart: "The chart behind the answer. Try the breakdown chips to slice it a different way.",

  insight: "The plain-English answer, the key numbers, and what to do next.",

  monitor: "Check KPIs and charts on the linked business monitor.",

  automate: "Review the exploratory follow-up workflow and estimated impact.",

};



export default function AnalyticsInsightSession() {

  const { catalogId } = useParams();

  const location = useLocation();

  const navigate = useNavigate();

  const { role: profileRole, profileId: activeProfileId } = useDemoProfile();
  const report = useReport(activeProfileId);
  const chatFilters = useChatFilters(activeProfileId);

  const periodSpan = useMemo(
    () =>
      resolveMiddlebyPeriodSpan(
        chatFilters.period,
        chatFilters.fromDate,
        chatFilters.toDate
      ),
    [chatFilters.period, chatFilters.fromDate, chatFilters.toDate]
  );

  const periodFactor = periodSpan.factor;
  const periodWindowLabel = periodSpan.label;

  const { entries } = useUseCaseCatalog();

  const entry = useMemo(

    () => enrichCatalogEntry(entries.find((e) => e.id === catalogId)),

    [entries, catalogId]

  );



  // "Track over time" tab visibility - previously gated on the shared monitor
  // manifest (only 15 of 50 use cases). Now driven by per-use-case content
  // from middleby-track-over-time so every Middleby use case gets a trend
  // view specific to itself, not a shared monitor.
  const hasTrackOverTime = useMemo(() => {
    if (!catalogId) return false;
    return Boolean(getMiddlebyTrackOverTime(catalogId));
  }, [catalogId]);

  // Kept for the (now-optional) "Open full monitor" cross-link surface.
  const monitorLink = useMemo(() => {
    if (!catalogId) return null;
    const monitor = getMonitorForCatalogId(catalogId);
    if (!monitor || monitor.phase !== "active") return null;
    return { path: monitorPath(monitor.id), title: monitor.title, id: monitor.id };
  }, [catalogId]);



  const automationPatterns = useMemo(

    () => (catalogId ? getPatternsForCatalogId(catalogId).filter((p) => p.poc) : []),

    [catalogId]

  );

  const visibleSessionViews = useMemo(

    () => {

      let views = SESSION_VIEWS;

      if (!hasTrackOverTime) views = views.filter((v) => v.id !== "monitor");

      if (automationPatterns.length === 0) views = views.filter((v) => v.id !== "automate");

      return views;

    },

    [hasTrackOverTime, automationPatterns.length]

  );



  const sessionRole = useMemo(() => normalizeDemoRoleId(profileRole), [profileRole]);

  const [activeView, setActiveView] = useState("insight");

  const [automatePatternId, setAutomatePatternId] = useState(null);




  useEffect(() => {

    if (automationPatterns.length === 0 && activeView === "automate") {

      setActiveView("insight");

    }

  }, [automationPatterns.length, activeView]);



  useEffect(() => {

    if (!hasTrackOverTime && activeView === "monitor") {

      setActiveView("insight");

    }

  }, [hasTrackOverTime, activeView]);



  useEffect(() => {

    if (!catalogId) return;

    logMiddlebyTelemetry({ type: "insight_session", uc_id: catalogId, role_lens: sessionRole });

  }, [catalogId, sessionRole]);



  const primarySpec = useMemo(() => (catalogId ? getMiddlebyPrimarySpec(catalogId) : null), [catalogId]);

  const drillSpecs = useMemo(() => (catalogId ? getDrillSpecs(catalogId) : []), [catalogId]);



  const [activeRole, setActiveRole] = useState("primary");

  const [loading, setLoading] = useState(true);

  const [dataset, setDataset] = useState({ blocked: false, rows: [], message: "" });

  const [primaryRows, setPrimaryRows] = useState([]);

  const [copiedIdx, setCopiedIdx] = useState(null);

  const [answer, setAnswer] = useState({ summary: "", items: [] });

  const [headlineLoading, setHeadlineLoading] = useState(true);

  const [brandDrawer, setBrandDrawer] = useState(null);

  const [skuSheet, setSkuSheet] = useState(null);



  const activeSpec = useMemo(() => {

    if (!catalogId) return null;

    if (activeRole === "primary") return getSpecByChartRole(catalogId, "primary");

    return getSpecByChartRole(catalogId, activeRole);

  }, [catalogId, activeRole]);

  const scaledDataset = useMemo(
    () => ({
      blocked: dataset.blocked,
      message: dataset.message,
      rows: scaleMiddlebyRowsForPeriod(dataset.rows, activeSpec, periodFactor),
    }),
    [dataset.blocked, dataset.message, dataset.rows, activeSpec, periodFactor]
  );



  useEffect(() => {

    let cancelled = false;

    async function load() {

      if (!activeSpec?.filePath) {

        setDataset({ blocked: true, rows: [], message: "No data file mapped for this chart." });

        setLoading(false);

        return;

      }

      setLoading(true);

      const data = await loadMiddlebyDataset(activeSpec.filePath);

      if (!cancelled) {

        setDataset({

          blocked: data.blocked,

          rows: data.rows || [],

          message: data.message || "",

        });

        setLoading(false);

      }

    }

    load();

    return () => {

      cancelled = true;

    };

  }, [activeSpec]);



  useEffect(() => {

    let cancelled = false;

    async function loadPrimary() {

      if (!primarySpec?.filePath) {

        if (!cancelled) setPrimaryRows([]);

        return;

      }

      const data = await loadMiddlebyDataset(primarySpec.filePath);

      if (!cancelled) setPrimaryRows(data.rows || []);

    }

    loadPrimary();

    return () => {

      cancelled = true;

    };

  }, [primarySpec]);



  useEffect(() => {

    if (!catalogId) return;

    logMiddlebyTelemetry({ type: "insight_open", uc_id: catalogId, role_lens: sessionRole });

  }, [catalogId, sessionRole]);



  useEffect(() => {

    let cancelled = false;

    async function loadHeadline() {

      if (!entry || !activeSpec) {

        setAnswer({ summary: "", items: [] });

        setHeadlineLoading(false);

        return;

      }

      setHeadlineLoading(true);

      const result = await resolveMiddlebyInsightHeadline(

        entry,

        activeSpec,

        scaledDataset.rows,

        sessionRole,

        catalogId,

        periodWindowLabel

      );

      if (!cancelled) {

        setAnswer(result);

        setHeadlineLoading(false);

      }

    }

    loadHeadline();

    return () => {

      cancelled = true;

    };

  }, [entry, activeSpec, scaledDataset.rows, sessionRole, catalogId, periodWindowLabel]);



  const kpis = useMemo(() => buildKpiChips(activeSpec, scaledDataset.rows), [activeSpec, scaledDataset.rows]);

  const actions = useMemo(

    () => buildRecommendedActions(entry, activeSpec, scaledDataset.rows),

    [entry, activeSpec, scaledDataset.rows]

  );



  const chartTitle = activeSpec?.chartType

    ? activeSpec.chartType.replace(/_/g, " ")

    : "Evidence";



  const [initialQuestionFromMount] = useState(() => {

    const q = location.state?.initialQuestion;

    return typeof q === "string" ? q.trim() : "";

  });



  const sessionHeadline =

    initialQuestionFromMount || entry?.sampleQuestion || entry?.problemSignal || "Insight Session";



  useEffect(() => {

    if (!location.state?.initialQuestion) return;

    const { initialQuestion: _ignored, ...rest } = location.state || {};

    navigate(location.pathname, { replace: true, state: rest });

  }, [location.pathname, location.state, navigate]);



  const handleAction = useCallback(async (action, idx) => {

    if (!action) return;

    if (action.type === "link" && action.href) {

      window.open(action.href, "_blank", "noopener,noreferrer");

      return;

    }

    if (action.type === "clipboard" && action.getText) {

      const text = action.getText();

      try {

        await navigator.clipboard.writeText(text);

        setCopiedIdx(idx);

        setTimeout(() => setCopiedIdx(null), 2000);

      } catch {

        /* ignore */

      }

    }

  }, []);



  const openBrand = useCallback(async (name) => {

    logMiddlebyTelemetry({ type: "brand_drawer", uc_id: catalogId, brand_name: name });

    const brand = await findMiddlebyBrand(name);

    if (brand) setBrandDrawer(brand);

  }, [catalogId]);



  const openSku = useCallback(async (sku) => {

    logMiddlebyTelemetry({ type: "sku_sheet", uc_id: catalogId, sku });

    const product = await findMiddlebyProduct(sku);

    if (product) setSkuSheet(product);

  }, [catalogId]);



  const selectDrillRole = useCallback(

    (role) => {

      setActiveRole(role);

      setActiveView("chart");

      logMiddlebyTelemetry({ type: "drill_through", uc_id: catalogId, chart_role: role });

    },

    [catalogId]

  );



  const selectView = useCallback(

    (viewId) => {

      setActiveView(viewId);

      logMiddlebyTelemetry({ type: "insight_view", uc_id: catalogId, view: viewId });

    },

    [catalogId]

  );



  const activeViewHint = SESSION_VIEW_HINTS[activeView] || "";



  const insightReturnPath = catalogId

    ? `/playground/insight/${encodeURIComponent(catalogId)}`

    : null;



  return (

    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">

      <Breadcrumb

        items={[

          { label: "Home", to: "/playground" },

          { label: "Use cases", to: "/analytics" },

          ...(location.state?.fromInsightId && location.state?.fromInsightTitle
            ? [{
                label: location.state.fromInsightTitle,
                to: `/playground/insight/${encodeURIComponent(location.state.fromInsightId)}`,
              }]
            : []),

          { label: entry?.title || "Insight Session" },

        ]}

      />

      {location.state?.fromInsightId && location.state?.fromInsightTitle ? (
        <button
          type="button"
          onClick={() =>
            navigate(
              `/playground/insight/${encodeURIComponent(location.state.fromInsightId)}`
            )
          }
          className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
        >
          ← Back to {location.state.fromInsightTitle}
        </button>
      ) : null}



      <div className="mt-4 border-b border-border pb-4">

        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">

          {entry?.title || "Use case"}

        </p>

        <h1 className="mt-1 text-base font-semibold leading-snug text-text-primary sm:text-lg">

          {sessionHeadline}

        </h1>



        <div className="mt-4 flex flex-wrap items-center gap-2">

          {visibleSessionViews.map(({ id, label, icon: Icon }) => (

            <button

              key={id}

              type="button"

              onClick={() => selectView(id)}

              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${

                activeView === id

                  ? "bg-primary text-white"

                  : "border border-border bg-surface-secondary text-text-secondary hover:text-text-primary"

              }`}

            >

              <Icon className="h-3.5 w-3.5" aria-hidden />

              {label}

              {id === "automate" && automationPatterns.length > 0 ? (

                <span

                  className={`rounded-full px-1.5 py-0.5 text-[10px] ${

                    activeView === id ? "bg-white/20" : "bg-primary/15 text-primary"

                  }`}

                >

                  {automationPatterns.length}

                </span>

              ) : null}

            </button>

          ))}

        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">

          <PeriodFilterChip chatFilters={chatFilters} />

          <span className="text-xs text-text-muted">Applies to the numbers in this use case.</span>

        </div>

        {activeViewHint ? (

          <p className="mt-3 text-sm text-text-secondary">{activeViewHint}</p>

        ) : null}

      </div>

      <InsightSessionToolbar
        entry={entry}
        spec={activeSpec}
        dataset={scaledDataset}
        chartTitle={chartTitle}
        catalogId={catalogId}
        windowLabel={periodWindowLabel}
        onPinBlocks={(items) => {
          for (const it of items) {
            report.pin({ block: it.block, sourceQuestion: it.sourceQuestion, catalogId });
          }
        }}
      />



      {activeView === "chart" ? (

        <div className="mt-6 space-y-4">

          <InsightSessionChartPanel

            activeSpec={activeSpec}

            dataset={scaledDataset}

            loading={loading}

            chartTitle={chartTitle}

            drillSpecs={drillSpecs}

            activeRole={activeRole}

            onSelectRole={selectDrillRole}

            crossSourceCaption={entry?.crossSourceCaption}

            designNotes={primarySpec?.designNotes}

            onBrandClick={openBrand}

            onSkuClick={openSku}

          />

        </div>

      ) : null}



      {activeView === "insight" ? (

        <div className="mt-6 space-y-5">

          <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">

            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">Answer</h2>

            <p className="mt-3 text-base leading-relaxed text-text-primary">

              {headlineLoading ? "Loading answer…" : answer.summary}

            </p>

            {answer.items.length > 0 ? (

              <ul className="mt-4 divide-y divide-border rounded-lg border border-border">

                {answer.items.slice(0, 6).map((item, i) => (

                  <li

                    key={`${item.label}-${i}`}

                    className="flex items-start justify-between gap-3 px-3 py-2.5 text-sm"

                  >

                    <div className="min-w-0">

                      <p className="font-medium text-text-primary">{item.label}</p>

                      {item.detail ? (

                        <p className="mt-0.5 text-xs text-text-muted">{item.detail}</p>

                      ) : null}

                    </div>

                    <div className="shrink-0 text-right">

                      <p className="font-semibold tabular-nums text-text-primary">{item.value}</p>

                      {item.metricLabel ? (

                        <p className="text-[10px] uppercase text-text-muted">{item.metricLabel}</p>

                      ) : null}

                    </div>

                  </li>

                ))}

              </ul>

            ) : null}

            {kpis.length > 0 ? (

              <div className="mt-4 flex flex-wrap gap-2">

                {kpis.map((chip) => (

                  <div

                    key={chip.label}

                    className="rounded-lg border border-primary/20 bg-primary-light px-3 py-2 dark:bg-primary/10"

                  >

                    <div className="text-[10px] font-semibold uppercase text-text-muted">{String(chip.label || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</div>

                    <div className="text-lg font-semibold tabular-nums text-text-primary">{chip.value}</div>

                  </div>

                ))}

              </div>

            ) : null}

            <button

              type="button"

              onClick={() => selectView("chart")}

              className="mt-4 text-xs font-semibold text-primary hover:underline"

            >

              View chart evidence

            </button>

            {PORTFOLIO_EXECUTIVE_CATALOG_IDS.includes(catalogId) ? (
              <div className="mt-4 border-t border-border pt-4">
                <Link
                  to={PORTFOLIO_PERFORMANCE_PATH}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary-light px-3 py-2 text-xs font-semibold text-primary hover:bg-primary/15 dark:bg-primary/10"
                >
                  <LayoutDashboard className="h-3.5 w-3.5" aria-hidden />
                  Open the full executive dashboard
                </Link>
                <p className="mt-2 text-xs text-text-muted">
                  Cross-portfolio KPIs, brand momentum, and category demand in one view — pulled from
                  HubSpot, GA4, and LogRocket for the selected window.
                </p>
              </div>
            ) : null}

          </section>



          {actions.length > 0 ? (

            <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">

              <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">

                Recommended actions

              </h2>

              <ol className="mt-3 list-none space-y-3 pl-0 text-sm text-text-secondary">

                {actions.map((a, i) => (

                  <li key={i} className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">

                    <span className="min-w-0 flex-1">{typeof a === "string" ? a : a.text}</span>

                    {typeof a === "object" && a.action ? (

                      <button

                        type="button"

                        onClick={() => handleAction(a.action, i)}

                        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-primary/30 bg-primary-light px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/15 dark:bg-primary/10"

                      >

                        {a.action.type === "link" ? (

                          <ExternalLink className="h-3.5 w-3.5" aria-hidden />

                        ) : (

                          <Copy className="h-3.5 w-3.5" aria-hidden />

                        )}

                        {copiedIdx === i ? "Copied" : a.action.label}

                      </button>

                    ) : null}

                  </li>

                ))}

              </ol>

            </section>

          ) : null}

        </div>

      ) : null}



      {activeView === "monitor" && hasTrackOverTime ? (

        <div className="mt-6">

          <InsightSessionMonitorPanel

            catalogId={catalogId}

            useCaseTitle={entry?.title}

            catalogEntry={entry}

            returnPath={insightReturnPath}

          />

        </div>

      ) : null}



      {activeView === "automate" ? (

        <div className="mt-6">

          <InsightSessionAutomationPanel
            catalogId={catalogId}
            patterns={automationPatterns}
            useCaseTitle={entry?.title}
            initialPatternId={automatePatternId}
          />

        </div>

      ) : null}



      {/* Legacy "How this dataset was built" footer removed - replaced by
          "Why you're seeing this" and "What Middleby needs to send" panels
          in the toolbar above, which are accessible from every tab. */}






      {brandDrawer ? <MiddlebyBrandDrawer brand={brandDrawer} onClose={() => setBrandDrawer(null)} /> : null}

      {skuSheet ? <MiddlebySkuSheet product={skuSheet} onClose={() => setSkuSheet(null)} /> : null}

    </div>

  );

}


