// Business Areas catalog. Lines of Business - each organizes its own processes, subject areas, and KPIs.
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import Breadcrumb from "./shared/Breadcrumb";
import { useBreadcrumbTrail, extendTrail } from "../hooks/useBreadcrumbTrail";
import {
 ArrowRight,
 BarChart3,
 Briefcase,
 ChartLine,
 Headphones,
 Monitor,
 Package,
 ShoppingCart,
 Store,
 Tag,
 TrendingUp,
 Users,
} from "lucide-react";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { useProcesses } from "../hooks/useProcesses";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
// Presentation metadata per area. Kept local so the core business-areas
// module stays a pure data registry.
const AREA_META = {
 inventory: {
 icon: Package,
 blurb: "Stock levels, replenishment, and loss prevention across stores and warehouses.",
 },
 orders: {
 icon: ShoppingCart,
 blurb: "End-to-end order flow: capture, fulfillment, delivery, and exceptions.",
 },
 merchandise: {
 icon: Tag,
 blurb: "Assortment planning, planogram compliance, and category performance.",
 },
 "store-ops": {
 icon: Store,
 blurb: "Daily store execution: labor, foot traffic, task compliance, KPI monitoring.",
 },
 "e-com": {
 icon: ChartLine,
 blurb: "Online storefront: traffic, conversion, product discovery, site performance.",
 },
 pos: {
 icon: Monitor,
 blurb: "Point-of-sale health: checkout efficiency, payment methods, staff performance.",
 },
 support: {
 icon: Headphones,
 blurb: "Customer support operations: returns, contact centre, wait times, escalations.",
 },
 pricing: {
 icon: TrendingUp,
 blurb: "Price positioning, markdown optimisation, competitor pricing, promotions.",
 },
 finance: {
 icon: BarChart3,
 blurb: "Enterprise finance: gross margin, shrinkage, budget vs actuals, cost efficiency.",
 },
 workforce: {
 icon: Users,
 blurb: "Workforce management: labor cost, overtime, scheduling compliance, productivity.",
 },
};
function kpiCountForArea(registry, areaId) {
 const categories = Array.isArray(registry?.categories) ? registry.categories : [];
 let n = 0;
 for (const cat of categories) {
 for (const section of cat?.sections || []) {
 for (const kpi of section?.kpis || []) {
 if (kpi?.active === false) continue;
 if (kpi.businessAreaId === areaId) n += 1;
 }
 }
 }
 return n;
}
export default function BusinessAreas() {
 const navigate = useNavigate();
 const trail = useBreadcrumbTrail("Business Areas", [{ label: "Playground", to: "/playground" }]);
 const { processes, hydrated: processesHydrated } = useProcesses();
 const { data: kpiRegistry, hydrated: kpiHydrated } = useKpiRegistry();
 const { areas: BUSINESS_AREAS } = useBusinessAreas();
 const ready = processesHydrated && kpiHydrated;
 const rows = useMemo(
 () =>
 BUSINESS_AREAS.map((ba) => {
 const meta = AREA_META[ba.id] || {};
 const kpiCount = kpiCountForArea(kpiRegistry, ba.id);
 const processCount = (processes || []).filter((p) => p.businessAreaId === ba.id).length;
 return {
 ba,
 icon: meta.icon || Briefcase,
 blurb: meta.blurb || "",
 kpiCount,
 processCount,
 };
 }),
 [processes, kpiRegistry, BUSINESS_AREAS]
 );
 if (!ready) {
 return (
 <div className="mx-auto max-w-[1440px] px-8 py-6">
 <p className="text-base leading-6 text-text-secondary">Loading business areas…</p>
 </div>
 );
 }
 return (
 <div className="mx-auto max-w-[1440px] px-8 py-6">
 <Breadcrumb items={trail} />
 <div className="mb-8 flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
 <div className="min-w-0 flex-1">
 <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
 Business Areas
 </h1>
 <p className="mt-1 text-base leading-6 text-text-secondary">
 Departments and divisions - each one organizes its own processes, subject areas, and KPIs. Use cases can cut across multiple business areas.
 </p>
 </div>
 </div>
 <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
 {rows.map(({ ba, icon: Icon, blurb, useCaseCount, kpiCount, processCount }) => (
 <article
 key={ba.id}
 className="flex min-w-0 flex-col gap-4 rounded-xl border border-border bg-surface p-5 shadow-sm transition-shadow hover:shadow-md"
 >
 <header className="flex items-center gap-3">
 <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-light dark:bg-primary/20">
 <Icon size={20} className="text-primary" strokeWidth={1.75} aria-hidden />
 </div>
 <div className="min-w-0">
 <span className="inline-flex items-center rounded-full bg-primary-light dark:bg-primary/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
 Department
 </span>
 <h2 className="mt-0.5 text-lg font-semibold text-text-primary">{ba.label}</h2>
 </div>
 </header>

 {blurb ? (
 <p className="text-sm leading-relaxed text-text-secondary">{blurb}</p>
 ) : null}

 {Array.isArray(ba.subjectAreas) && ba.subjectAreas.length > 0 ? (
 <div className="rounded-lg border border-[#fde8d8] bg-[#fff9f5] px-3 py-2.5">
 <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[#b93815]">
 Functional areas
 </p>
 <div className="flex flex-wrap gap-1.5">
 {ba.subjectAreas.map((s) => (
 <span
 key={s}
 className="rounded-full bg-surface px-2 py-0.5 text-xs font-medium text-text-tertiary ring-1 ring-[#f1d4bd]"
 >
 {s}
 </span>
 ))}
 </div>
 </div>
 ) : null}

 <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-[#f2f2f5] pt-3">
 <div className="flex flex-wrap items-center gap-3 text-xs text-text-secondary">
 <span className="inline-flex items-center gap-1">
 <ArrowRight size={13} strokeWidth={2} className="text-primary" aria-hidden />
 <span className="font-semibold text-text-primary">{processCount}</span> process{processCount === 1 ? "" : "es"}
 </span>
 <span className="text-[#d5d7da]">|</span>
 <span className="inline-flex items-center gap-1">
 <BarChart3 size={13} strokeWidth={2} className="text-primary" aria-hidden />
 <span className="font-semibold text-text-primary">{kpiCount}</span> KPI{kpiCount === 1 ? "" : "s"}
 </span>
 </div>
 <div className="flex flex-wrap items-center gap-2">
 <button
 type="button"
 onClick={() => navigate(`/processes#area-${ba.id}`, { state: { trail: extendTrail(trail, "Business Areas", "/business-areas") } })}
 className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs font-semibold text-text-tertiary transition hover:border-primary hover:text-primary"
 >
 View processes
 <ArrowRight size={12} strokeWidth={2.5} aria-hidden />
 </button>

 <button
 type="button"
 onClick={() => navigate(`/kpis/registry?area=${encodeURIComponent(ba.id)}`, {
 state: { trail: extendTrail(trail, "Business Areas", "/business-areas") },
 })}
 className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs font-semibold text-text-tertiary transition hover:border-primary hover:text-primary"
 >
 View KPIs
 <ArrowRight size={12} strokeWidth={2.5} aria-hidden />
 </button>
 </div>
 </div>
 </article>
 ))}
 </div>
 </div>
 );
}
