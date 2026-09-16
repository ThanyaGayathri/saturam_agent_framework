import { useEffect, useMemo, useRef, useState } from "react";
import { FileText, Inbox, RefreshCw, Sparkles, Trash2, Upload, X } from "lucide-react";
import PlatformRunPanel from "./PlatformRunPanel";
import { acceptGoldenCaseOutput, createGoldenCase, deleteGoldenCase, fetchGoldenCases, fetchMailboxMessages, updateGoldenCase } from "../../services/use-cases-api";
import { findTemplate } from "../../domain/extraction-templates";
import CaseExpectedEditor from "./CaseExpectedEditor";

/**
 * The three ways a rehearsal gets its documents (K19): chosen messages from the connected
 * mailbox, documents the person uploads, or the set the platform generates from the
 * template. Every tab runs the same compiled workflow with writes held and renders the same
 * result. Uploaded and generated documents are golden cases: they keep what the person expects
 * of them, are re-run whenever the use case changes, and their pass rate gates activation.
 */
const TABS = [
 { id: "mailbox", label: "From the mailbox", icon: Inbox },
 { id: "uploads", label: "Upload documents", icon: Upload },
 { id: "generated", label: "Generated samples", icon: Sparkles },
];
const STATUS = { pass: "bg-status-success-light text-status-success", fail: "bg-status-critical-light text-status-critical", untested: "bg-surface-secondary text-text-secondary" };

function CaseList({ cases, selected, onToggle, onDelete, onAccept, onSetExpected, editing, setEditing, spec, template, emptyText }) {
 if (!cases.length) return <p className="text-sm text-text-muted">{emptyText}</p>;
 return (
  <ul className="divide-y divide-border rounded-lg border border-border">
   {cases.map((c) => (
    <li key={c.id} className="px-3 py-2 text-sm">
     <div className="flex flex-wrap items-center gap-2">
      <input type="checkbox" checked={selected.has(c.id)} onChange={() => onToggle(c.id)} aria-label={c.label} />
      <FileText size={13} className="text-text-muted" />
      <span className="font-medium text-text-primary">{c.label}</span>
      <span className="text-xs text-text-muted">{c.sender ? `from ${c.sender}` : ""}</span>
      <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS[c.last_status] || STATUS.untested}`}>{c.last_status}{c.expected ? "" : " · nothing expected yet"}</span>
      {c.expected?.outcome && <span className="font-mono text-[11px] text-text-muted" title={JSON.stringify(c.expected.extracted || {})}>expects {c.expected.outcome}{c.expected.extracted && Object.keys(c.expected.extracted).length ? ` · ${Object.keys(c.expected.extracted).length} value(s)` : ""}</span>}
      {onSetExpected && <button type="button" onClick={() => setEditing(editing === c.id ? null : c.id)} className="text-[11px] font-semibold text-primary hover:underline">{c.expected ? "Edit expected" : "Set expected"}</button>}
      {onAccept && c.last_result?.observed && c.last_status !== "pass" && <button type="button" onClick={() => onAccept(c.id)} className="text-[11px] font-semibold text-primary hover:underline">Accept last output as expected</button>}
      {onDelete && <button type="button" onClick={() => onDelete(c.id)} className="text-text-muted hover:text-status-critical" aria-label={`Remove ${c.label}`}><Trash2 size={12} /></button>}
     </div>
     {editing === c.id && <CaseExpectedEditor spec={spec} template={template} caseRow={c} onSave={(expected) => { onSetExpected(c.id, expected); setEditing(null); }} onCancel={() => setEditing(null)} />}
    </li>
   ))}
  </ul>
 );
}

export default function RehearsalSources({ spec, ensureUseCase }) {
 const [tab, setTab] = useState(spec.mailbox ? "mailbox" : "uploads");
 const [useCaseId, setUseCaseId] = useState(spec.platform?.useCaseId || null);
 const [messages, setMessages] = useState(null);
 const [messagesError, setMessagesError] = useState(null);
 const [picked, setPicked] = useState(new Set());
 const [cases, setCases] = useState([]);
 const [pickedCases, setPickedCases] = useState(new Set());
 const [note, setNote] = useState("");
 const fileRef = useRef(null);
 const [sender, setSender] = useState("");
 const controller = useRef(null);
 useEffect(() => () => controller.current?.abort(), []);

 const platformId = async (signal) => {
  if (useCaseId) return useCaseId;
  const id = await ensureUseCase({ signal });
  setUseCaseId(id);
  return id;
 };
 const refreshCases = async (id = useCaseId) => { if (!id) return; try { setCases(await fetchGoldenCases(id)); } catch (err) { setNote(`Could not list the cases: ${err?.detail || err?.message}`); } };
 const loadMessages = async () => {
  setMessagesError(null); setMessages(null);
  try { const id = await platformId(); setMessages(await fetchMailboxMessages(id)); }
  catch (err) { setMessages([]); setMessagesError(err?.status === 503 ? `The mailbox is not reachable: ${err.detail}` : err?.detail || err?.message || "The platform did not answer."); }
 };
 useEffect(() => { if (tab === "mailbox" && messages === null) loadMessages(); if (tab !== "mailbox") platformId().then(refreshCases).catch(() => {}); // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [tab]);

 const uploaded = useMemo(() => cases.filter((c) => c.source === "uploaded"), [cases]);
 const generated = useMemo(() => cases.filter((c) => c.source === "generated"), [cases]);
 const togglePicked = (set, setter) => (id) => setter((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
 const onFiles = async (files) => {
  const id = await platformId();
  for (const file of files) {
   const b64 = await new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1] || ""); r.readAsDataURL(file); });
   try { await createGoldenCase(id, { label: file.name.replace(/\.pdf$/i, ""), sender: sender.trim(), subject: file.name.replace(/\.pdf$/i, ""), filename: file.name, document_b64: b64 }); }
   catch (err) { setNote(`${file.name}: ${err?.detail || err?.message}`); }
  }
  await refreshCases(id);
 };
 const remove = async (caseId) => { await deleteGoldenCase(useCaseId, caseId); await refreshCases(); };
 const accept = async (caseId) => { try { await acceptGoldenCaseOutput(useCaseId, caseId); await refreshCases(); } catch (err) { setNote(err?.detail || err?.message || "Could not accept."); } };
 const setExpected = async (caseId, expected) => { try { await updateGoldenCase(useCaseId, caseId, { expected }); await refreshCases(); } catch (err) { setNote(err?.detail || err?.message || "Could not save the expected output."); } };
 const [editing, setEditing] = useState(null);
 const template = findTemplate(spec);
 const summary = (list) => { const withExpected = list.filter((c) => c.expected); const pass = withExpected.filter((c) => c.last_status === "pass").length; const fail = withExpected.filter((c) => c.last_status === "fail").length; return { withExpected: withExpected.length, pass, fail }; };

 const panelFor = (source, ids, label) => (
  <PlatformRunPanel
   key={source}
   compact
   mode="rehearse"
   useCaseId={useCaseId}
   beforeRun={async ({ signal }) => platformId(signal)}
   request={async ({ useCaseId: id }) => {
    if (source === "mailbox") return { source: "mailbox", message_ids: [...picked] };
    if (source === "generated") return { source: "generated", case_ids: pickedCases.size ? [...pickedCases].filter((c) => generated.some((g) => g.id === c)) : undefined };
    return { source: "cases", case_ids: pickedCases.size ? [...pickedCases].filter((c) => uploaded.some((u) => u.id === c)) : uploaded.map((c) => c.id) };
   }}
   onFinished={() => refreshCases()}
   onAccept={source === "mailbox" ? null : accept}
   createdAs={spec.createdAs}
   buttonLabel={label}
   disabled={source === "mailbox" ? picked.size === 0 : source === "uploads" ? uploaded.length === 0 : false}
   disabledReason={source === "mailbox" ? "Tick at least one message." : source === "uploads" ? "Upload a document first." : undefined}
  />
 );

 return (
  <section className="rounded-xl border border-border bg-surface p-4">
   <h2 className="text-sm font-bold text-text-primary">Rehearse: run the compiled workflow with every write held</h2>
   <p className="mt-1 text-sm text-text-secondary">The real extraction model, your master data, your guardrails, your mapping. Nothing is written to {spec.writeback || "the target"} and nothing is sent. Choose what to run it over.</p>
   <div className="mt-3 flex flex-wrap gap-1.5" role="tablist">
    {TABS.map(({ id, label, icon: Icon }) => <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)} className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${tab === id ? "border-primary bg-primary text-white" : "border-border-light text-text-secondary hover:border-primary hover:text-primary"}`}><Icon size={12} /> {label}</button>)}
   </div>
   {note && <p className="mt-2 text-xs text-status-critical">{note} <button type="button" onClick={() => setNote("")} aria-label="Dismiss"><X size={11} className="inline" /></button></p>}

   {tab === "mailbox" && (
    <div className="mt-3 space-y-3">
     <p className="text-sm text-text-secondary">The messages in {spec.mailbox || "the mailbox"} that match the filter, as they are now. Tick the ones to run. Nothing is flagged or moved.</p>
     {messages === null && !messagesError && <p className="text-sm text-text-muted">Reading the mailbox…</p>}
     {messagesError && <p className="text-sm text-status-warning">{messagesError}</p>}
     {Array.isArray(messages) && messages.length === 0 && !messagesError && <p className="text-sm text-text-muted">No messages match the filter right now.</p>}
     {Array.isArray(messages) && messages.length > 0 && (
      <>
       <div className="flex items-center gap-3 text-xs">
        <button type="button" onClick={() => setPicked(new Set(messages.map((m) => m.id)))} className="font-semibold text-primary hover:underline">Select all {messages.length}</button>
        <button type="button" onClick={() => setPicked(new Set())} className="text-text-muted hover:underline">Clear</button>
        <button type="button" onClick={loadMessages} className="ml-auto inline-flex items-center gap-1 text-text-muted hover:text-primary"><RefreshCw size={11} /> Refresh</button>
       </div>
       <ul className="max-h-72 divide-y divide-border overflow-y-auto rounded-lg border border-border">
        {messages.map((m) => (
         <li key={m.id} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-sm">
          <input type="checkbox" checked={picked.has(m.id)} onChange={() => togglePicked(picked, setPicked)(m.id)} aria-label={m.subject} />
          <span className="font-medium text-text-primary">{m.subject}</span>
          <span className="text-xs text-text-muted">from {m.sender}{m.size ? ` · ${Math.round(m.size / 1024)} KB` : ""}</span>
          {m.processed_before && <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-[10px] font-semibold text-text-muted">already processed by a live run</span>}
         </li>
        ))}
       </ul>
      </>
     )}
     {panelFor("mailbox", null, `Rehearse ${picked.size ? `${picked.size} selected` : "selected"}`)}
    </div>
   )}

   {tab === "uploads" && (
    <div className="mt-3 space-y-3">
     <p className="text-sm text-text-secondary">Your own documents, as golden cases. Two ways to say what each should produce: type it first with "Set expected", or run it, look at the output, and accept it. They are re-run whenever the template, guardrails or mapping change, and their pass rate gates activation.</p>
     <div className="flex flex-wrap items-center gap-2">
      <input ref={fileRef} type="file" accept="application/pdf" multiple className="hidden" onChange={(e) => { onFiles([...(e.target.files || [])]); e.target.value = ""; }} />
      <button type="button" onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-border px-3 py-1.5 text-xs font-semibold text-text-secondary hover:border-primary hover:text-primary"><Upload size={12} /> Upload PDFs</button>
      <label className="text-xs text-text-muted">treated as sent from <input value={sender} onChange={(e) => setSender(e.target.value)} placeholder="buyer@customer.example (optional)" className="ml-1 w-64 rounded-md border border-border bg-surface px-2 py-1 text-xs" /></label>
      <span className="text-[11px] text-text-muted">The sender's domain is how the customer is found; leave it empty to test the unknown-sender path.</span>
     </div>
     {uploaded.length > 0 && (() => { const s = summary(uploaded); return <p className="text-xs text-text-secondary">{uploaded.length} uploaded · {s.withExpected} with expected output · {s.pass} pass · {s.fail} fail</p>; })()}
     <CaseList cases={uploaded} selected={pickedCases} onToggle={togglePicked(pickedCases, setPickedCases)} onDelete={remove} onAccept={accept} onSetExpected={setExpected} editing={editing} setEditing={setEditing} spec={spec} template={template} emptyText="No documents uploaded yet." />
     {panelFor("uploads", null, pickedCases.size ? "Rehearse selected" : "Rehearse all uploaded")}
    </div>
   )}

   {tab === "generated" && (
    <div className="mt-3 space-y-3">
     <p className="text-sm text-text-secondary">Documents the platform invents from the template's own fields and your master data: a few clean ones, one per guardrail that trips it, one the resolver cannot place, one scanned copy. Each carries the outcome it expects. Useful on day one, before any real document; proof of the mechanics, not of a real supplier's layout.</p>
     {generated.length > 0 && (() => { const s = summary(generated); return <p className="text-xs text-text-secondary">{generated.length} generated · {s.pass} pass · {s.fail} fail{s.withExpected - s.pass - s.fail ? ` · ${s.withExpected - s.pass - s.fail} not run yet` : ""}</p>; })()}
     <CaseList cases={generated} selected={pickedCases} onToggle={togglePicked(pickedCases, setPickedCases)} onDelete={remove} onAccept={accept} onSetExpected={setExpected} editing={editing} setEditing={setEditing} spec={spec} template={template} emptyText="Nothing generated yet; the first run generates the set." />
     {panelFor("generated", null, generated.length ? (pickedCases.size ? "Rehearse selected" : "Rehearse the generated set") : "Generate the samples and rehearse")}
    </div>
   )}
  </section>
 );
}
