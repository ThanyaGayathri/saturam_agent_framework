import { useState } from "react";
import { ArrowLeft, Sparkles } from "lucide-react";

/**
 * Step 2 of the AI path: a chat-style composer asking what the dashboard should help track.
 * Styled like the product's other assistant/composer pairs (`PlaygroundAssistantMessage.jsx`,
 * `ChatComposerTextarea.jsx`) so it reads as native chat, not a bolted-on form.
 */
export default function AiPurposeStep({ initialPurpose, onBack, onGenerate }) {
 const [purpose, setPurpose] = useState(initialPurpose || "");
 const canGenerate = purpose.trim().length > 0;

 return (
  <>
   <div className="mb-4 flex items-center justify-between">
    <button
     type="button"
     onClick={onBack}
     className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
    >
     <ArrowLeft size={13} />
     Change mode
    </button>
    <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary">
     <Sparkles size={11} />
     AI generated
    </span>
   </div>

   <div className="mb-4 flex gap-3">
    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-white">
     <Sparkles size={13} />
    </div>
    <p className="pt-1 text-[15px] leading-relaxed text-text-primary">
     What should this dashboard help you track? Describe the purpose in a sentence or two —
     I'll suggest metrics and a filter to match.
    </p>
   </div>

   <div className="mb-2.5 rounded-lg border border-border px-3.5 py-3">
    <textarea
     autoFocus
     rows={3}
     value={purpose}
     onChange={(e) => setPurpose(e.target.value)}
     placeholder="e.g. Track weekly engagement across our APAC accounts so I can catch drop-offs before quarterly reviews."
     className="w-full resize-none bg-transparent text-sm leading-relaxed text-text-primary placeholder:text-text-muted focus:outline-none"
    />
    <div className="mt-2 flex items-center justify-end">
     <button
      type="button"
      disabled={!canGenerate}
      onClick={() => onGenerate(purpose.trim())}
      className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
     >
      <Sparkles size={14} />
      Generate
     </button>
    </div>
   </div>
   <p className="text-xs leading-relaxed text-text-muted">
    Tip: mention the audience, cadence and any region or segment focus — it sharpens the
    suggestions.
   </p>
  </>
 );
}
