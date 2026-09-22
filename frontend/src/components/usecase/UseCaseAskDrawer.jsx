import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, MessageSquare, Send, X } from "lucide-react";
import { AnswerBlockRenderer } from "../shared/answer-blocks";
import { CHAT_MODES } from "../../storage/chat-mode";
import { askUseCase, fetchAskQuestions } from "../../services/use-cases-api";

/**
 * Ask the use case (K26 demo): a drawer from the right where a person asks one of the ten
 * questions the at-risk data answers -- typed, or picked from the chips -- and gets the
 * playground's answer blocks back: a headline, findings, a chart, a table, the plays,
 * where the numbers came from. Every figure is computed on the use case's data and its
 * latest run; nothing here calls a model.
 */
export default function UseCaseAskDrawer({ useCaseId, title, open, onClose }) {
 const [questions, setQuestions] = useState([]);
 const [messages, setMessages] = useState([]); // {role: user|assistant, text?, meta?}
 const [draft, setDraft] = useState("");
 const [busy, setBusy] = useState(false);
 const bottom = useRef(null);

 useEffect(() => {
  if (!open) return undefined;
  fetchAskQuestions(useCaseId).then(setQuestions).catch(() => setQuestions([]));
  const onKey = (e) => { if (e.key === "Escape") onClose(); };
  window.addEventListener("keydown", onKey);
  const previous = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = previous; };
 }, [open, useCaseId, onClose]);

 useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

 const ask = useCallback(async (question) => {
  const q = String(question || "").trim();
  if (!q || busy) return;
  setDraft("");
  setMessages((m) => [...m, { role: "user", text: q }]);
  setBusy(true);
  try {
   const answer = await askUseCase(useCaseId, q);
   setMessages((m) => [...m, { role: "assistant", meta: { variant: "answer-block", ...answer } }]);
  } catch (err) {
   setMessages((m) => [...m, { role: "assistant", text: err?.detail || err?.message || "The platform did not answer." }]);
  } finally {
   setBusy(false);
  }
 }, [useCaseId, busy]);

 if (!open) return null;
 const latestIndex = messages.reduce((idx, m, i) => (m.role === "assistant" ? i : idx), -1);
 return createPortal(
  <div className="fixed inset-0 z-[100]" role="dialog" aria-modal="true" aria-label={`Ask ${title}`}>
   <div className="absolute inset-0 bg-black/30" onClick={onClose} />
   <aside className="absolute inset-y-0 right-0 flex w-full max-w-[720px] flex-col border-l border-border bg-surface shadow-2xl">
    <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
     <div className="min-w-0">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">Ask the use case</p>
      <p className="truncate text-sm font-semibold text-text-primary">{title}</p>
     </div>
     <button type="button" onClick={onClose} aria-label="Close" className="rounded-md border border-border px-2 py-1 text-xs font-semibold text-text-secondary hover:text-text-primary"><X size={12} /></button>
    </div>

    <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
     {messages.length === 0 && (
      <div className="rounded-xl border border-dashed border-border bg-surface-secondary/60 px-4 py-3">
       <p className="text-sm text-text-primary">Ask about the at-risk pipeline -- the quotes, the accounts, the reasons, the plays.</p>
       <p className="mt-0.5 text-xs text-text-secondary">Every answer is computed on this use case's data and its latest run. Try one:</p>
       <div className="mt-2 flex flex-wrap gap-1.5">
        {questions.map((q) => <button key={q.id} type="button" onClick={() => ask(q.label)} className="rounded-full border border-primary/40 bg-surface px-2.5 py-1 text-xs font-semibold text-primary hover:bg-primary-light">{q.label}</button>)}
       </div>
      </div>
     )}
     {messages.map((m, i) => m.role === "user" ? (
      <div key={i} className="flex justify-end"><p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-white">{m.text}</p></div>
     ) : m.meta ? (
      <div key={i} className="rounded-xl border border-border bg-surface px-4 py-3">
       <AnswerBlockRenderer meta={m.meta} chatMode={CHAT_MODES.FULL} hideFollowUps={i !== latestIndex} onFollowUpSelect={ask} animate={false} />
      </div>
     ) : (
      <p key={i} className="rounded-xl border border-status-critical-border bg-status-critical-light px-3 py-2 text-sm text-status-critical">{m.text}</p>
     ))}
     {busy && <p className="inline-flex items-center gap-2 text-xs text-text-muted"><Loader2 size={12} className="animate-spin" /> Reading the pipeline…</p>}
     <div ref={bottom} />
    </div>

    <form onSubmit={(e) => { e.preventDefault(); ask(draft); }} className="border-t border-border px-4 py-3">
     {messages.length > 0 && (
      <div className="mb-2 flex flex-wrap gap-1.5">
       {questions.slice(0, 5).map((q) => <button key={q.id} type="button" onClick={() => ask(q.label)} className="rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] font-semibold text-text-secondary hover:border-primary hover:text-primary">{q.label}</button>)}
      </div>
     )}
     <div className="flex items-center gap-2">
      <MessageSquare size={14} className="shrink-0 text-text-muted" />
      <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Ask about the quotes at risk…" className="flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />
      <button type="submit" disabled={!draft.trim() || busy} className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40"><Send size={13} /> Ask</button>
     </div>
    </form>
   </aside>
  </div>,
  document.body,
 );
}
