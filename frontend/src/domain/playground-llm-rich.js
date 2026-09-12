import { PLAYGROUND_TOPIC_PANELS } from "./playground-chat-replies";
import { sanitizeLlmText } from "./sanitize-llm-text";

const TOPICS = new Set(["inventory", "store_ops", "orders"]);

function cleanField(value, max) {
 const s = sanitizeLlmText(value ?? "");
 return max && s.length > max ? s.slice(0, max - 1).trimEnd() + "…" : s;
}

/**
 * @param {Record<string, unknown>} data
 * @returns {{ layout: "rich", intro: string, topic: string, panel: { findings: Array<{title: string, value: string, change: string, desc: string}>, recommendations: string[] } } | null}
 */
function parseRichPayload(data) {
 const intro = cleanField(data.intro, 0);
 if (!intro) return null;

 let topic = String(data.topic ?? "inventory").trim();
 if (!TOPICS.has(topic)) topic = "inventory";

 const defaultPanel = PLAYGROUND_TOPIC_PANELS[topic] || PLAYGROUND_TOPIC_PANELS.inventory;

 let findings = Array.isArray(data.findings) ? data.findings : [];
 findings = findings.slice(0, 3).map((f, i) => ({
 title: cleanField(f?.title ?? defaultPanel.findings[i]?.title ?? "Metric", 96),
 value: cleanField(f?.value ?? defaultPanel.findings[i]?.value ?? "-", 40),
 change: cleanField(f?.change ?? defaultPanel.findings[i]?.change ?? "", 28),
 desc: cleanField(f?.desc ?? defaultPanel.findings[i]?.desc ?? "", 240),
 }));
 while (findings.length < 3) {
 const i = findings.length;
 const d = defaultPanel.findings[i];
 if (d) {
 findings.push({ ...d });
 } else {
 findings.push({
 title: "Follow-up",
 value: "-",
 change: "",
 desc: "See narrative above.",
 });
 }
 }

 let recommendations = Array.isArray(data.recommendations)
 ? data.recommendations.map((r) => cleanField(r, 200)).filter(Boolean)
 : [];
 recommendations = recommendations.slice(0, 3);
 while (recommendations.length < 3) {
 const i = recommendations.length;
 recommendations.push(
 defaultPanel.recommendations[i] ?? "Align with your store or DC lead on the next step."
 );
 }

 return { layout: "rich", intro, topic, panel: { findings, recommendations } };
}

/**
 * Parse model JSON: either compact plain reply or rich KPI panel payload.
 * Supports legacy responses with no `layout` but with `intro` + `findings` (treated as rich).
 *
 * @param {string} raw
 * @returns {{ layout: "rich", intro: string, topic: string, panel: { findings: Array<{title: string, value: string, change: string, desc: string}>, recommendations: string[] } } | { layout: "compact", text: string } | null}
 */
export function parsePlaygroundStructuredLlmResponse(raw) {
 if (!raw || typeof raw !== "string") return null;
 let s = raw.trim();
 const fence = s.match(/^```(?:json)?\s*([\s\S]*?)```$/i);
 if (fence) s = fence[1].trim();
 let data;
 try {
 data = JSON.parse(s);
 } catch {
 return null;
 }
 if (!data || typeof data !== "object") return null;

 const layoutRaw = String(data.layout ?? "").trim().toLowerCase();

 if (layoutRaw === "compact") {
 const body = sanitizeLlmText(data.body ?? data.text ?? "");
 if (!body) return null;
 return { layout: "compact", text: body };
 }

 if (layoutRaw === "rich" || layoutRaw === "") {
 const rich = parseRichPayload(data);
 if (rich) return rich;
 const body = sanitizeLlmText(data.body ?? data.text ?? "");
 if (body) return { layout: "compact", text: body };
 return null;
 }

 const body = sanitizeLlmText(data.body ?? data.text ?? "");
 if (body) return { layout: "compact", text: body };
 return parseRichPayload(data);
}
