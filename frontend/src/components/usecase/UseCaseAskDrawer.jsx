import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, MessageSquare, Send, X } from "lucide-react";
import { AnswerBlockRenderer } from "../shared/answer-blocks";
import MarkdownProse from "../shared/answer-blocks/MarkdownProse";
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

 const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

 /** Reveal something over `ms`, driven by the clock rather than by a tick count: a
  *  throttled tab (a background window) jumps to the end instead of dragging. */
 const revealOver = useCallback(async (ms, apply) => {
  const started = Date.now();
  for (;;) {
   const t = Math.min(1, (Date.now() - started) / ms);
   apply(t);
   if (t >= 1) return;
   await sleep(30);
  }
 }, []);

 /** The answer arrives whole; it is shown the way it was made: the steps as they run, the
  *  narrative as it is written, then each block as it is drawn. */
 const ask = useCallback(async (question) => {
  const q = String(question || "").trim();
  if (!q || busy) return;
  setDraft("");
  setMessages((m) => [...m, { role: "user", text: q }]);
  setBusy(true);
  const id = Date.now();
  const patch = (fn) => setMessages((m) => m.map((x) => (x.id === id ? fn(x) : x)));
  setMessages((m) => [...m, { id, role: "assistant", steps: [], done: [], narrative: "", meta: null, shown: 0 }]);
  try {
   const started = Date.now();
   const pending = askUseCase(useCaseId, q);
   patch((x) => ({ ...x, steps: ["Reading the question"] }));
   const answer = await pending;
   const computedMs = Date.now() - started;
   const trace = ["Reading the question", ...(answer.trace || [])];

   // The steps, one after another.
   await revealOver(220 * trace.length, (t) => {
    const upto = Math.max(1, Math.ceil(t * trace.length));
    patch((x) => ({ ...x, steps: trace.slice(0, upto), done: Array.from({ length: Math.max(0, upto - 1) }, (_, i) => i) }));
   });
   patch((x) => ({ ...x, steps: trace, done: trace.map((_, i) => i), elapsedMs: computedMs }));

   // The narrative, word by word, markdown kept balanced while a line is still arriving.
   const text = answer.narrative || answer.intro || "";
   const words = text.split(/(\s+)/);
   await revealOver(Math.min(2200, 26 * text.split(" ").length), (t) => {
    const partial = words.slice(0, Math.ceil(t * words.length)).join("");
    const stars = (partial.match(/\*\*/g) || []).length;
    patch((x) => ({ ...x, narrative: stars % 2 ? partial + "**" : partial }));
   });
   patch((x) => ({ ...x, narrative: text }));

   // Then the blocks, one at a time.
   const meta = { variant: "answer-block", ...answer, blocks: (answer.blocks || []).filter((b) => b.type !== "insight" && b.type !== "findings") };
   patch((x) => ({ ...x, meta, shown: 0 }));
   const total = meta.blocks.length + 1;
   await revealOver(320 * total, (t) => patch((x) => ({ ...x, shown: Math.ceil(t * total) })));
   patch((x) => ({ ...x, shown: total }));
  } catch (err) {
   patch((x) => ({ ...x, error: err?.detail || err?.message || "The platform did not answer." }));
  } finally {
   setBusy(false);
  }
 }, [useCaseId, busy, revealOver]);

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
     ) : (
      <div key={m.id || i} className="rounded-xl border border-border bg-surface px-4 py-3">
       {m.steps?.length > 0 && (
        <ol className="mb-2 space-y-0.5">
         {m.steps.map((step, k) => { const finished = m.done?.includes(k) || m.narrative; return (
          <li key={k} className="flex items-center gap-2 text-[11px] text-text-muted">
           {finished ? <Check size={11} className="text-status-success" /> : <Loader2 size={11} className="animate-spin text-primary" />}
           <span className={finished ? "" : "text-text-secondary"}>{step}</span>
          </li>
         ); })}
         {m.elapsedMs != null && m.narrative && <li className="pt-0.5 text-[10px] text-text-muted">computed in {(m.elapsedMs / 1000).toFixed(1)}s · every figure from the data</li>}
        </ol>
       )}
       {m.error && <p className="text-sm text-status-critical">{m.error}</p>}
       {m.narrative && <div className="border-t border-border pt-2"><MarkdownProse>{m.narrative}</MarkdownProse>{!m.meta && <span className="ml-0.5 inline-block h-3.5 w-0.5 animate-pulse bg-primary align-middle" />}</div>}
       {m.meta && m.shown > 0 && (
        <div className="mt-2">
         <AnswerBlockRenderer meta={{ ...m.meta, blocks: m.meta.blocks.slice(0, m.shown) }} chatMode={CHAT_MODES.FULL} hideFollowUps={i !== latestIndex || m.shown <= m.meta.blocks.length} onFollowUpSelect={ask} animate={false} />
        </div>
       )}
      </div>
     ))}
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
