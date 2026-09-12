/**
 * Chat completions go through POST /api/llm/chat-completions so base URL, API key, and model stay server-side
 * (RETAILGPT_LLM_* on Vercel / dev .env - not in the browser bundle).
 *
 * Debugging: optional VITE_LLM_DEBUG=true logs non-secret metadata (no prompts) to the console.
 */

import { parsePlaygroundStructuredLlmResponse } from "../domain/playground-llm-rich";
import { sanitizeLlmText } from "../domain/sanitize-llm-text";

const STATUS_CACHE_MS = 30_000;
let configuredCache = { at: 0, value: false };

/** Verbose logging (metadata only - never logs API keys or full prompts). */
export function isRetailLlmDebugEnabled() {
 const v = (import.meta.env.VITE_LLM_DEBUG || "").toLowerCase();
 return v === "true" || v === "1" || v === "yes";
}

function llmLog(...args) {
 if (isRetailLlmDebugEnabled()) console.log("[RetailGPT LLM]", ...args);
}

function llmError(...args) {
 console.error("[RetailGPT LLM]", ...args);
}

/**
 * Whether the server reports LLM env is set (cached ~30s).
 */
export async function fetchRetailLlmConfigured() {
 const now = Date.now();
 if (now - configuredCache.at < STATUS_CACHE_MS) {
 return configuredCache.value;
 }
 try {
 const r = await fetch("/api/llm/status", { credentials: "include" });
 const data = await r.json().catch(() => ({}));
 configuredCache = { at: now, value: data.configured === true };
 return configuredCache.value;
 } catch {
 configuredCache = { at: now, value: false };
 return false;
 }
}

const RETAILGPT_RULES = `You are RetailGPT, an assistant for retail and wholesale operators. You focus on inventory, supply chain, DCs and fulfillment, stores, e-commerce, pricing, labor, compliance, and B2B relationships.

Rules:
- Always respond with helpful, concrete retail-relevant guidance. Never refuse to answer; if details are missing, state reasonable assumptions briefly and still answer.
- Honor the timeline lens when provided (near-term execution vs quarterly / structural).
- Speak as if you understand this is a product demo; avoid claiming live access to the customer's systems unless the user implies it.
- When you give numbers or KPI-style figures in structured outputs, treat them as illustrative demo-style signals (directional / plausible), not live system facts, unless the user explicitly frames them as real data.

Formatting (very important, applies to every string you output):
- Plain text only. No Markdown syntax anywhere: no **bold**, no *italics*, no underscores for emphasis, no backtick code spans, no fenced code blocks, no hashes for headings, no Markdown tables.
- No hyphens or asterisks to simulate bullets unless asked. Use short sentences separated by a blank line.
- No emojis, no ASCII art, no horizontal rules. Write how a retail operator would speak in a briefing.`;

const PLAYGROUND_STRUCTURED_JSON_SUFFIX = `Output format (required): respond with a single JSON object only. No markdown fences, no commentary before or after the JSON, no trailing text.

Required key: "layout" - either "rich" or "compact". Choose based on the question:
- Use "compact" (default for most turns) for: brief or narrow questions, follow-ups, clarifications, yes/no answers, definitions, opinions, quick recommendations, small calculations, conversational replies, and anything the user could answer in a sentence or two.
- Use "rich" only when the user asks for a dashboard-style overview, a performance summary, KPI / metric breakdown, "what should I look at first", regional or hub-level scan, or clearly wants multiple metric tiles.

When "layout" is "compact":
- Include "body": string. Keep it to about 1 to 3 short sentences (roughly 2 lines on screen) for simple questions. For genuinely complex compact answers, up to 2 short paragraphs is acceptable. Never include Markdown characters (**, *, _, \`, #, >, |, fenced code). Plain sentences only. Do not invent fake metric tables. A single blank line between paragraphs is fine.

When "layout" is "rich":
- "intro": string, 1 or 2 short sentences in a demo tone that sets up the metric tiles. Plain text only. No Markdown. You may end with a colon.
- "topic": one of "inventory", "store_ops", "orders" - best match for the question.
- "findings": array of exactly 3 objects, each with "title", "value", "change", "desc" (all plain strings, no Markdown). Use plausible illustrative demo metrics. "change" is a short delta or comparison like "+3.2%" or "vs plan".
- "recommendations": array of exactly 3 short imperative actions, plain strings, no leading dashes or numbering.

Ground every answer in the active customer configuration you were given (business areas, processes, use cases, KPIs). When the user asks about something outside that set, still answer but note briefly that it is not currently tracked for this customer.

Your entire message must be valid JSON parseable by JSON.parse. Every string value must be plain text with no Markdown.`;

/**
 * @param {{ userQuestion: string, customerName: string, timeline?: string, catalogId?: string, businessArea?: string, subjectArea?: string, transcriptMessages?: Array<{ role: string, content: string }> | null, profileContext?: string | null }} ctx
 * @param {{ systemAppend?: string, structured?: boolean }} opts
 * @returns {{ messages: Array<{ role: string, content: string }>, max_tokens: number } | null}
 */
function buildPlaygroundChatMessages(ctx, opts = {}) {
 const systemAppend = (opts.systemAppend || "").trim();
 const structured = Boolean(opts.structured);
 const customerName = (ctx.customerName || "Retail customer").trim();
 const timeline = (ctx.timeline || "Not specified").trim();
 const catalogNote = ctx.catalogId ? `Catalog / use-case id (if any): ${ctx.catalogId}\n` : "";
 const businessArea = typeof ctx.businessArea === "string" ? ctx.businessArea.trim() : "";
 const subjectArea = typeof ctx.subjectArea === "string" ? ctx.subjectArea.trim() : "";
 const areaContextLines = [
 businessArea ? `- Business area focus: ${businessArea}` : "",
 subjectArea ? `- Subject area focus: ${subjectArea}` : "",
 ].filter(Boolean).join("\n");
 const transcriptMessages = Array.isArray(ctx.transcriptMessages) ? ctx.transcriptMessages : null;
 const profileContext = (ctx.profileContext || "").trim();

 if (!transcriptMessages?.length) {
 const question = (ctx.userQuestion || "").trim();
 if (!question) {
 llmLog("Not called: empty user question");
 return null;
 }
 }

 const appendBlock = systemAppend ? `\n\n${systemAppend}` : "";
 const profileBlock = profileContext
 ? `\n\nCustomer configuration (use this as the primary source for KPIs, processes, and use cases the customer has today):\n${profileContext}`
 : "";

 /** @type {Array<{ role: string, content: string }>} */
 let messages;

 if (transcriptMessages?.length) {
 const last = transcriptMessages[transcriptMessages.length - 1];
 if (last?.role !== "user") {
 llmError("Transcript must end with a user message; got:", last?.role);
 }
 const system = `${RETAILGPT_RULES}

Session context:
- Customer / demo profile name: ${customerName}
- Timeline lens (apply especially to the latest user turn): ${timeline}
${ctx.catalogId ? `- Catalog / use-case id: ${ctx.catalogId}` : ""}${areaContextLines ? `\n${areaContextLines}` : ""}${profileBlock}

You are in a multi-turn conversation. Use the full message history for context. The latest user message is the final user turn. If the user replies briefly (e.g. "yes", "what about the Midwest?", "Q2?"), infer intent from prior turns and answer in a retail-relevant way.${appendBlock}`;

 messages = [{ role: "system", content: system }, ...transcriptMessages];
 } else {
 const question = (ctx.userQuestion || "").trim();
 const user = `${catalogNote}Customer / demo profile name: ${customerName}
Timeline lens: ${timeline}${areaContextLines ? `\n${areaContextLines}` : ""}

User question:
${question}`;
 messages = [
 { role: "system", content: `${RETAILGPT_RULES}${profileBlock}${appendBlock}` },
 { role: "user", content: user },
 ];
 }

 const baseMax = transcriptMessages?.length ? 1200 : 900;
 const max_tokens = structured ? Math.min(Math.max(baseMax, 1200), 1600) : baseMax;

 return { messages, max_tokens };
}

/**
 * @param {Array<{ role: string, content: string }>} messages
 * @param {{ max_tokens: number, temperature: number, response_format?: { type: string } }} req
 * @returns {Promise<string | null>}
 */
async function postChatCompletion(messages, req) {
 /** @type {Record<string, unknown>} */
 const body = {
 messages,
 temperature: req.temperature,
 max_tokens: req.max_tokens,
 };
 if (req.response_format) {
 body.response_format = req.response_format;
 }

 llmLog("Proxy POST /api/llm/chat-completions", {
 messageCount: messages.length,
 max_tokens: req.max_tokens,
 temperature: req.temperature,
 structured: Boolean(req.response_format),
 });

 let res;
 try {
 res = await fetch("/api/llm/chat-completions", {
 method: "POST",
 credentials: "include",
 headers: { "Content-Type": "application/json" },
 body: JSON.stringify(body),
 });
 } catch (err) {
 llmError("Network / fetch failed (offline or server error):", err);
 throw err;
 }

 const rawText = await res.text().catch(() => "");

 if (res.status === 401) {
 llmError("LLM proxy returned 401 - sign in if this deployment requires prototype login.");
 throw new Error("LLM unauthorized");
 }

 if (!res.ok) {
 llmError(`HTTP ${res.status} ${res.statusText}`, rawText.slice(0, 4000) || "(empty body)");
 throw new Error(rawText || `LLM request failed (${res.status})`);
 }

 let data;
 try {
 data = JSON.parse(rawText);
 } catch (e) {
 llmError("Response was not JSON. First 800 chars:", rawText.slice(0, 800));
 throw e;
 }

 llmLog("Response JSON keys:", data && typeof data === "object" ? Object.keys(data) : typeof data);

 const text = data?.choices?.[0]?.message?.content;
 const out = typeof text === "string" ? text.trim() : "";
 if (!out) {
 llmError("No assistant text in response. choices[0].message.content missing or empty.", {
 choices: data?.choices,
 error: data?.error,
 });
 return null;
 }

 llmLog("Assistant text length:", out.length, "preview:", out.slice(0, 280) + (out.length > 280 ? "…" : ""));
 return out;
}

/**
 * @param {{ userQuestion: string, customerName: string, timeline?: string, catalogId?: string, transcriptMessages?: Array<{ role: string, content: string }> | null }} ctx
 * When `transcriptMessages` is set, it must be the full thread ending with the latest user turn (system is prepended here).
 * @returns {Promise<string | null>} Assistant text, or null if not configured / empty response
 */
export async function fetchRetailLlmAssistantText(ctx) {
 const configured = await fetchRetailLlmConfigured();
 if (!configured) {
 llmLog("Not called: server reports LLM not configured (RETAILGPT_LLM_* env). See frontend/.env.example.");
 return null;
 }

 const built = buildPlaygroundChatMessages(ctx);
 if (!built) return null;

 const raw = await postChatCompletion(built.messages, {
 max_tokens: built.max_tokens,
 temperature: 0.55,
 });
 return raw ? sanitizeLlmText(raw) : raw;
}

/**
 * Same context as `fetchRetailLlmAssistantText`, but asks the model for JSON: either compact "body" or rich KPI panel.
 * @param {{ userQuestion: string, customerName: string, timeline?: string, catalogId?: string, transcriptMessages?: Array<{ role: string, content: string }> | null }} ctx
 * @returns {Promise<{ layout: "rich", intro: string, topic: string, panel: { findings: Array<{title: string, value: string, change: string, desc: string}>, recommendations: string[] } } | { layout: "compact", text: string } | null>}
 */
export async function fetchRetailLlmPlaygroundStructured(ctx) {
 const configured = await fetchRetailLlmConfigured();
 if (!configured) {
 llmLog("Structured: not called: server LLM not configured");
 return null;
 }

 const compactHint =
 ctx.hintPreferCompactFollowup === true
 ? `\n\nThread hint: the assistant message before this user turn used KPI metric tiles. Prefer "layout": "compact" unless the user clearly asks for a new metrics summary, KPIs, or dashboard-style view.`
 : "";
 const built = buildPlaygroundChatMessages(ctx, {
 systemAppend: PLAYGROUND_STRUCTURED_JSON_SUFFIX + compactHint,
 structured: true,
 });
 if (!built) return null;

 let raw;
 try {
 raw = await postChatCompletion(built.messages, {
 max_tokens: built.max_tokens,
 temperature: 0.45,
 response_format: { type: "json_object" },
 });
 } catch (e) {
 llmLog("Structured: request failed (will fall back to plain text path if caller retries)", e);
 return null;
 }

 if (!raw) return null;
 const parsed = parsePlaygroundStructuredLlmResponse(raw);
 if (!parsed) {
 llmError("Structured: could not parse model JSON; preview:", raw.slice(0, 400));
 } else {
 llmLog("Structured layout:", parsed.layout);
 }
 return parsed;
}
