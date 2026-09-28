import { buildProfileContextForLlm } from "./build-profile-context";
import { demoProfileLabel } from "./demo-profiles";
import { getPlaygroundAssistantReply } from "./playground-chat-replies";
import { collapseAdjacentDuplicateUserMessages } from "./chat-message-utils";
import {
 fetchRetailLlmAssistantText,
 fetchRetailLlmConfigured,
 fetchRetailLlmPlaygroundStructured,
 isRetailLlmDebugEnabled,
} from "../services/retail-llm";
import { tryMiddlebyPlaygroundDataGrounding } from "./middleby-playground-data-grounding";
import { tryMiddlebyAnswerBlockRouter } from "./middleby-answer-block-router";
import { isBlocksPreviewTrigger, buildBlocksPreviewReply } from "./answer-blocks/preview-message";
import { MIDDLEBY_PROFILE_ID } from "./demo-profiles";

/** One-time warning in production when the LLM is unreachable. */
let warnedLlmNotConfiguredInProd = false;

function timelineFromContext(exploreHub, userMeta) {
 const lens = userMeta?.lens ?? exploreHub?.lens;
 const h = userMeta?.horizon ?? exploreHub?.horizon;
 if (lens && String(lens).trim()) return String(lens).trim();
 if (h === "week") return "This week";
 if (h === "quarter") return "This quarter";
 return "";
}

/** True if the turn before the latest user message was a rich (KPI tiles) assistant reply. */
function assistantBeforeLatestUserWasRich(collapsed) {
 const last = collapsed[collapsed.length - 1];
 if (last?.role !== "user") return false;
 for (let i = collapsed.length - 2; i >= 0; i--) {
 const m = collapsed[i];
 if (m.role === "assistant") return m.meta?.variant === "playground-rich";
 if (m.role === "user") return false;
 }
 return false;
}

function formatUserContentForLlm(m) {
 const t = String(m.text || "").trim();
 if (!t) return "";
 const lens =
 m.meta?.lens ||
 (m.meta?.horizon === "week" ? "This week" : m.meta?.horizon === "quarter" ? "This quarter" : null);
 if (lens) return `[${lens}]\n${t}`;
 return t;
}

/** DB/chat rows → OpenAI-style turns (includes seed + prior user/assistant). */
function conversationToTranscriptMessages(rows) {
 const collapsed = collapseAdjacentDuplicateUserMessages(rows || []);
 const out = [];
 for (const m of collapsed) {
 if (m.role === "user") {
 const c = formatUserContentForLlm(m);
 if (c) out.push({ role: "user", content: c });
 } else if (m.role === "assistant") {
 let c = String(m.text || "").trim();
 if (m.meta?.variant === "playground-rich" && m.meta?.panel?.findings?.length) {
 const hint = m.meta.panel.findings
 .map((f) => `${String(f.title || "").trim()}: ${String(f.value || "").trim()}`)
 .join("; ");
 if (c && hint) c = `${c}\n[Illustrative KPI tiles in UI: ${hint}]`;
 else if (hint) c = `[Illustrative KPI tiles in UI: ${hint}]`;
 }
 if (c) out.push({ role: "assistant", content: c });
 }
 }
 return out;
}

/**
 * Playground assistant reply: Middleby data grounding when a question routes to a known UC,
 * then LLM when configured, else offline scripted fallback.
 *
 * @param {string} userText
 * @param {string} [profileId]
 * @param {{ catalogId?: string, horizon?: string, lens?: string } | undefined} [exploreHub]
 * @param {{ lens?: string, horizon?: string, catalogId?: string } | undefined} [userMeta]
 * @param {Array<{ role: string, text: string, meta?: object }> | null | undefined} [conversationSnapshot] rows after the latest user message was persisted
 * @returns {Promise<{ text: string, meta?: object }>}
 */

export async function resolvePlaygroundAssistantReplyAsync(
 userText,
 profileId,
 exploreHub,
 userMeta,
 conversationSnapshot = null
) {
 // Dev-only slash command: /blocks-preview renders every answer-block type.
 // Gated to the Middleby profile so it can't accidentally appear elsewhere.
 if (profileId === MIDDLEBY_PROFILE_ID && isBlocksPreviewTrigger(userText)) {
  return buildBlocksPreviewReply();
 }

 // Middleby hero use cases: return rich answer-block responses before falling
 // through to the text-only data grounding path.
 const heroBlockReply = await tryMiddlebyAnswerBlockRouter(
   userText,
   profileId,
   conversationSnapshot,
   { period: userMeta?.period, fromDate: userMeta?.fromDate, toDate: userMeta?.toDate }
 );
 if (heroBlockReply) return heroBlockReply;

 const fallback = getPlaygroundAssistantReply(userText, profileId, exploreHub);

 // If our scripted reply is already a rich (structured) response — for
 // example a Media profile explore-hub reply that resolves to one of the
 // MEDIA_TOPIC_PANELS — return it directly instead of letting the LLM
 // downgrade it to plain text.
 if (fallback?.meta?.variant === "playground-rich") {
   return fallback;
 }

 const dataGrounded = await tryMiddlebyPlaygroundDataGrounding(
  userText,
  profileId,
  exploreHub,
  userMeta
 );
 if (dataGrounded) return dataGrounded;

 const configured = await fetchRetailLlmConfigured();

 if (!configured) {
 if (!warnedLlmNotConfiguredInProd && import.meta.env.PROD) {
 warnedLlmNotConfiguredInProd = true;
 console.warn(
 "[RetailGPT] LLM is off: set server-side RETAILGPT_LLM_API_BASE_URL and RETAILGPT_LLM_API_KEY (and Azure fields if needed) in Vercel → Environment Variables or frontend/.env - not VITE_* - then redeploy / restart dev. See frontend/.env.example."
 );
 }
 console.warn(
 "[RetailGPT LLM] Using offline scripted reply because /api/llm/status reports configured=false. Set RETAILGPT_LLM_* env vars and restart the dev server (or redeploy)."
 );
 return fallback;
 }

 const collapsed = conversationSnapshot?.length
 ? collapseAdjacentDuplicateUserMessages(conversationSnapshot)
 : [];
 const transcriptMessages = conversationToTranscriptMessages(conversationSnapshot || []);
 const customerName = demoProfileLabel(profileId);
 const timeline = timelineFromContext(exploreHub, userMeta);
 const catalogId = userMeta?.catalogId ?? exploreHub?.catalogId;
 const profile = buildProfileContextForLlm(profileId);

 // Latest-wins area context. The current turn's meta wins; if it does not
 // carry businessArea / subjectArea (e.g. the user typed a free-form
 // follow-up), inherit each field from the most recent prior user turn that
 // had one. Falls through to undefined so the prompt builder simply omits
 // the line.
 let businessArea = userMeta?.businessArea || undefined;
 let subjectArea = userMeta?.subjectArea || undefined;
 if ((!businessArea || !subjectArea) && Array.isArray(conversationSnapshot)) {
 for (let i = conversationSnapshot.length - 1; i >= 0; i--) {
 const row = conversationSnapshot[i];
 if (!row || row.role !== "user" || !row.meta) continue;
 if (!businessArea && typeof row.meta.businessArea === "string" && row.meta.businessArea.trim()) {
 businessArea = row.meta.businessArea.trim();
 }
 if (!subjectArea && typeof row.meta.subjectArea === "string" && row.meta.subjectArea.trim()) {
 subjectArea = row.meta.subjectArea.trim();
 }
 if (businessArea && subjectArea) break;
 }
 }

 if (isRetailLlmDebugEnabled()) {
 console.log("[RetailGPT LLM] Invoking model…", {
 mode: transcriptMessages.length >= 2 ? "multi-turn" : "single-turn",
 transcriptTurns: transcriptMessages.length,
 userText: (userText || "").slice(0, 500),
 profileId,
 customerName,
 timeline: timeline || "Not specified",
 catalogId: catalogId || null,
 businessArea: businessArea || null,
 subjectArea: subjectArea || null,
 profileContextChars: profile?.text?.length || 0,
 });
 }

 const llmCtx = {
 userQuestion: userText,
 customerName,
 timeline: timeline || "Not specified",
 catalogId,
 businessArea,
 subjectArea,
 transcriptMessages: transcriptMessages.length > 0 ? transcriptMessages : null,
 hintPreferCompactFollowup: assistantBeforeLatestUserWasRich(collapsed),
 profileContext: profile?.text || null,
 };

 try {
 const structured = await fetchRetailLlmPlaygroundStructured(llmCtx);
 if (structured) {
 if (structured.layout === "compact") {
 return { text: structured.text, meta: undefined };
 }
 return {
 text: structured.intro,
 meta: {
 variant: "playground-rich",
 topic: structured.topic,
 panel: structured.panel,
 },
 };
 }

 const llmText = await fetchRetailLlmAssistantText(llmCtx);
 if (llmText) {
 return { text: llmText, meta: undefined };
 }
 console.warn(
 "[RetailGPT LLM] Model returned empty/null text; using offline scripted reply. Check /api/llm/chat-completions response in the Network tab and server logs."
 );
 } catch (e) {
 console.error(
 "[RetailGPT LLM] Call failed; using offline scripted reply. Check /api/llm/chat-completions in the Network tab.",
 e
 );
 }

 return fallback;
}
