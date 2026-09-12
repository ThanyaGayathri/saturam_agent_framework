import { AlertCircle, AlertTriangle, ShieldX } from "lucide-react";
import {
  EXPLORE_PROMPT_SIGNAL_LABELS,
  inferExplorePromptSignal,
} from "../../domain/demo-use-case-prompts";

const WRAPPER = "inline-flex shrink-0 items-center justify-center [&_svg]:shrink-0";

/** Matches `colors.danger.DEFAULT` in tailwind.config.js. Explicit stroke avoids gray from parent `text-text-muted`. */
const CRITICAL_STROKE = "#dc2626";

/**
 * Severity-style icon aligned with prompt meaning (horizon/lens or explicit `signal` on the prompt).
 */
export function ExplorePromptSeverityIcon({ prompt, indexInCard = 0 }) {
  const signal = inferExplorePromptSignal(prompt, indexInCard);
  const label = EXPLORE_PROMPT_SIGNAL_LABELS[signal] ?? EXPLORE_PROMPT_SIGNAL_LABELS.attention;

  if (signal === "critical") {
    return (
      <span className={WRAPPER} title={label}>
        <ShieldX size={14} strokeWidth={2.25} stroke={CRITICAL_STROKE} className="shrink-0" aria-hidden />
        <span className="sr-only">{label}</span>
      </span>
    );
  }
  if (signal === "warning") {
    return (
      <span className={WRAPPER} title={label}>
        <AlertTriangle size={14} strokeWidth={2.25} stroke="#F79009" className="shrink-0" aria-hidden />
        <span className="sr-only">{label}</span>
      </span>
    );
  }
  return (
    <span className={WRAPPER} title={label}>
      <AlertCircle size={14} strokeWidth={2.25} stroke="#c2410c" className="shrink-0" aria-hidden />
      <span className="sr-only">{label}</span>
    </span>
  );
}
