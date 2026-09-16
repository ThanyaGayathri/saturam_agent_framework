import { useState } from "react";
import { ArrowLeft, Sparkles } from "lucide-react";

/**
 * "Add KPI" -> AI generated: the request, in the person's own words. Submitting starts a
 * suggestion run with it as the `ask` (contract 1.2.3) -- the same run "Suggest KPIs" starts,
 * steered by the request -- and closes this modal: the run's progress and its proposals show
 * on the discovery page itself, each with "Add to my metrics", exactly as an unprompted run's
 * do. Nothing is created here; a proposal becomes a KPI only when it is adopted.
 *
 * Styled like `create-dashboard/AiPurposeStep.jsx`'s composer so the two AI entry points read
 * as the same kind of thing.
 */
export default function AddKpiAiStep({ focusAreaLabels = [], onBack, onStart }) {
 const [ask, setAsk] = useState("");
 const canStart = ask.trim().length > 0;

 return (
  <>
   <button
    type="button"
    onClick={onBack}
    className="mb-4 inline-flex items-center gap-1 text-xs font-semibold text-text-secondary hover:text-text-primary"
   >
    <ArrowLeft size={13} />
    Change how
   </button>

   <p className="mb-1 text-sm font-semibold text-text-primary">What do you want to measure?</p>
   <p className="mb-3 text-xs leading-relaxed text-text-muted">
    The model reads your connected data and proposes the KPIs that answer this, checked against
    the tables you actually have. Proposals land on this page with "Add to my metrics" — nothing
    is added until you pick one.
    {focusAreaLabels.length > 0 && (
     <>
      {" "}Your current focus ({focusAreaLabels.join(", ")}) still applies.
     </>
    )}
   </p>

   <div className="mb-2.5 rounded-lg border border-border px-3.5 py-3">
    <textarea
     autoFocus
     rows={4}
     maxLength={2000}
     value={ask}
     onChange={(e) => setAsk(e.target.value)}
     onKeyDown={(e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canStart) onStart(ask.trim());
     }}
     placeholder="e.g. How many quote requests turn into deals each week, by brand — and which brands are slipping."
     className="w-full resize-none bg-transparent text-sm leading-relaxed text-text-primary placeholder:text-text-muted focus:outline-none"
     aria-label="What do you want to measure?"
    />
    <div className="mt-2 flex items-center justify-between gap-2">
     <span className="text-[11px] text-text-muted">{ask.length} / 2000</span>
     <button
      type="button"
      disabled={!canStart}
      onClick={() => onStart(ask.trim())}
      className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
     >
      <Sparkles size={14} />
      Suggest KPIs
     </button>
    </div>
   </div>
   <p className="text-xs leading-relaxed text-text-muted">
    Tip: say what decision the number should inform, the breakdown you need ("by store", "per
    brand") and the cadence ("weekly", "last 30 days"). A run takes a minute or two.
   </p>
  </>
 );
}
