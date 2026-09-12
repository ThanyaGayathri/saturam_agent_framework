/**
 * Serializes the active profile's processes, use cases, and KPIs (with their business area and
 * process mapping links) into a compact text block that grounds LLM replies.
 *
 * The assistant uses this to reference only things this customer actually has configured, and to
 * keep numbers / recommendations consistent with the visible registries.
 */

import { BUSINESS_AREAS } from "./business-areas";
import { demoProfileLabel, MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "./demo-profiles";
import { loadProcesses } from "../storage/processes";
import { loadUseCaseCatalog } from "../storage/use-cases-catalog";
import { loadKpiRegistry } from "../storage/kpi-registry";
import { getBusinessAreasSync } from "../storage/business-areas";
import { storageKeysForProfile } from "../storage/keys";

const MAX_USE_CASES = 18;
const MAX_KPIS = 22;

function truncate(s, n) {
 const t = String(s || "").trim();
 if (t.length <= n) return t;
 return `${t.slice(0, n - 1).trimEnd()}…`;
}

function resolveBusinessAreas(profileId, storageKey) {
 try {
 const areas = getBusinessAreasSync(storageKey, profileId);
 if (areas?.length) return areas;
 } catch {
 /* fall through */
 }
 return BUSINESS_AREAS;
}

function businessAreaLabelById(id, areas) {
 const pool = areas || BUSINESS_AREAS;
 const ba = pool.find((b) => b.id === id);
 return ba ? ba.label : "";
}

function summarizeProcesses(processes) {
 if (!processes?.length) return "";
 return processes
 .map((p) => {
 const name = truncate(p.name, 60);
 const desc = truncate(p.description || p.impact || "", 140);
 return `- ${p.id} ${name}${desc ? ` - ${desc}` : ""}`;
 })
 .join("\n");
}

function summarizeUseCases(entries) {
 const rows = (entries || []).slice(0, MAX_USE_CASES);
 if (!rows.length) return "";
 return rows
 .map((e) => {
 const title = truncate(e.title, 80);
 const area = e.businessArea ? ` [${e.businessArea}]` : "";
 const proc = e.process ? ` (process: ${e.process})` : "";
 const signal = e.problemSignal ? ` - ${truncate(e.problemSignal, 120)}` : "";
 return `- ${title}${area}${proc}${signal}`;
 })
 .join("\n");
}

function flattenKpis(registry) {
 const out = [];
 const categories = Array.isArray(registry?.categories) ? registry.categories : [];
 for (const cat of categories) {
 const sections = Array.isArray(cat?.sections) ? cat.sections : [];
 for (const section of sections) {
 const kpis = Array.isArray(section?.kpis) ? section.kpis : [];
 for (const kpi of kpis) {
 if (kpi?.active === false) continue;
 out.push({
 name: kpi.name,
 unit: kpi.unit,
 businessAreaId: kpi.businessAreaId,
 subjectArea: kpi.subjectArea,
 level: kpi.level,
 processMapping: Array.isArray(kpi.processMapping) ? kpi.processMapping : [],
 industryBenchmark: kpi.industryBenchmark,
 });
 }
 }
 }
 return out;
}

function summarizeKpis(registry, areas) {
 const flat = flattenKpis(registry).slice(0, MAX_KPIS);
 if (!flat.length) return "";
 return flat
 .map((k) => {
 const ba = businessAreaLabelById(k.businessAreaId, areas);
 const areaBits = [ba, k.subjectArea].filter(Boolean).join(" / ");
 const proc = k.processMapping.length ? ` ← ${k.processMapping.join(", ")}` : "";
 const unit = k.unit ? ` [${k.unit}]` : "";
 const bench = k.industryBenchmark ? ` (benchmark: ${truncate(k.industryBenchmark, 40)})` : "";
 return `- ${truncate(k.name, 70)}${unit}${areaBits ? ` - ${areaBits}` : ""}${proc}${bench}`;
 })
 .join("\n");
}

/**
 * Build a compact profile-context string for LLM system prompts. Reads localStorage synchronously; safe in the browser.
 *
 * @param {string} profileId
 * @returns {{ profileLabel: string, text: string } | null}
 */
export function buildProfileContextForLlm(profileId) {
 try {
 const id = normalizeDemoProfileId(profileId);
 const keys = storageKeysForProfile(id);

 const processesState = loadProcesses(keys.PROCESSES, id);
 const catalogState = loadUseCaseCatalog(keys.USE_CASE_CATALOG, id);
 const kpiState = loadKpiRegistry(keys.KPI_REGISTRY, id);
 const areas = resolveBusinessAreas(id, keys.BUSINESS_AREAS);

 const processesBlock = summarizeProcesses(processesState?.processes || []);
 const useCasesBlock = summarizeUseCases(catalogState?.entries || []);
 const kpiBlock = summarizeKpis(kpiState || {}, areas);

 if (!processesBlock && !useCasesBlock && !kpiBlock) return null;

 const areaList = areas.map((b) => b.label).join(", ");
 const sections = [];
 sections.push(`Business areas (navigation): ${areaList}.`);
 if (processesBlock) sections.push(`Active processes (id - name - description):\n${processesBlock}`);
 if (useCasesBlock) sections.push(`Configured use cases (title [business area] (process) - problem signal):\n${useCasesBlock}`);
 if (kpiBlock) sections.push(`Tracked KPIs (name [unit] - business area / subject ← processes):\n${kpiBlock}`);
 if (id === MIDDLEBY_PROFILE_ID) {
 sections.push(
 "Middleby analytics context: connected sources are GA4 (shop sessions, funnels, brand traffic), HubSpot (quotes, timeframe, consultation, repeat vs new brand), and LogRocket (JS errors on shop and RFQ paths). Reply in an analytics tone: headline insight first, cite illustrative KPI tiles when helpful, then 2-4 dealer or digital ops actions. Do not use retail inventory, DC, or store ops language unless the user explicitly asks."
 );
 } else {
 sections.push(
 "When the user asks for analysis, prefer referencing KPIs, processes, and use cases from the lists above. If a user asks about something not in the lists, answer helpfully and briefly note it is not currently tracked for this customer. Use the P# ids in parentheses when it helps trace a KPI to a process."
 );
 }

 return {
 profileLabel: demoProfileLabel(id),
 text: sections.join("\n\n"),
 };
 } catch {
 return null;
 }
}
