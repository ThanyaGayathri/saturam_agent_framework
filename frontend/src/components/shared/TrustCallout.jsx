import { ShieldCheck } from "lucide-react";

/**
 * Small persistent trust/privacy note used at the top of the four functional
 * screens the guided tour visits (Playground, KPI Registry, Use Cases,
 * Configure). Reads cleanly on its own, and doubles as an anchor the tour
 * highlights via `data-tour="{tourAnchor}"`.
 *
 * Keep the copy short and plain. The tour voiceover expands the idea; the
 * on-page text just gives viewers something to read at their own pace.
 */
export default function TrustCallout({ text, tourAnchor }) {
  return (
    <div
      data-tour={tourAnchor}
      className="inline-flex items-center gap-2 rounded-full border border-[#a9f0d1] bg-[#ecfdf3]/70 px-3 py-1.5 text-xs font-medium text-[#067647] shadow-[0_1px_1px_rgba(16,24,40,0.04)] dark:border-[#067647]/40 dark:bg-[#067647]/12 dark:text-[#6ce9a6]"
    >
      <ShieldCheck className="h-3.5 w-3.5 shrink-0" strokeWidth={2} aria-hidden />
      <span>{text}</span>
    </div>
  );
}
