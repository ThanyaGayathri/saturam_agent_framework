import { useState } from "react";
import { CheckCircle2, AlertTriangle, XCircle, ChevronDown, ChevronRight, ShieldCheck } from "lucide-react";

/**
 * Pre-flight checklist rendered before the Approve CTA in the Automate step.
 * Same status-pill + expandable-detail grammar as UCBuilderStep7PreflightCheck.
 *
 * Props:
 *   checks: [{ id, title, status: "pass" | "warn" | "fail", detail: string }]
 *   acknowledged?: boolean
 *   onAcknowledgeChange?: (val) => void
 */

const STATUS = {
  pass: {
    Icon: CheckCircle2,
    iconColor: "text-[#067647]",
    badge: "bg-[#ecfdf3] text-[#067647]",
    label: "Pass",
    border: "border-[#a7f3d0]",
  },
  warn: {
    Icon: AlertTriangle,
    iconColor: "text-[#b54708]",
    badge: "bg-[#fffaeb] text-[#b54708]",
    label: "Warning",
    border: "border-[#fde68a]",
  },
  fail: {
    Icon: XCircle,
    iconColor: "text-[#b42318]",
    badge: "bg-[#fef3f2] text-[#b42318]",
    label: "Blocker",
    border: "border-[#fca5a5]",
  },
};

export default function AutomationPreflightChecks({
  checks = [],
  acknowledged = false,
  onAcknowledgeChange,
}) {
  const [expandedId, setExpandedId] = useState(null);

  if (!checks.length) return null;

  const hasWarnings = checks.some((c) => c.status === "warn");
  const hasBlockers = checks.some((c) => c.status === "fail");
  const allPass = !hasWarnings && !hasBlockers;

  const toggle = (id) => setExpandedId((prev) => (prev === id ? null : id));

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-4 w-4 text-primary" aria-hidden />
        <h4 className="text-sm font-semibold text-text-primary">Pre-flight checks</h4>
      </div>

      <div
        className={`flex items-center gap-3 rounded-lg border px-3 py-2 ${
          hasBlockers
            ? "border-[#fca5a5] bg-[#fef3f2]"
            : hasWarnings
            ? "border-[#fde68a] bg-[#fffaeb]"
            : "border-[#a7f3d0] bg-[#ecfdf3]"
        }`}
      >
        {hasBlockers ? (
          <XCircle className="h-4 w-4 shrink-0 text-[#b42318]" aria-hidden />
        ) : hasWarnings ? (
          <AlertTriangle className="h-4 w-4 shrink-0 text-[#b54708]" aria-hidden />
        ) : (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-[#067647]" aria-hidden />
        )}
        <p className="text-sm font-medium text-text-primary">
          {hasBlockers
            ? "Resolve blockers before approving."
            : hasWarnings
            ? "Warnings detected. Acknowledge to proceed."
            : "All checks passed. Ready to approve."}
        </p>
      </div>

      <ul className="space-y-2">
        {checks.map((check) => {
          const cfg = STATUS[check.status] ?? STATUS.pass;
          const isExpanded = expandedId === check.id;
          const isClickable = check.status !== "pass";
          return (
            <li key={check.id}>
              <div
                className={`overflow-hidden rounded-lg border bg-surface-default ${cfg.border} ${
                  isClickable ? "cursor-pointer" : ""
                }`}
                onClick={isClickable ? () => toggle(check.id) : undefined}
                role={isClickable ? "button" : undefined}
                tabIndex={isClickable ? 0 : undefined}
                onKeyDown={(e) => {
                  if (isClickable && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault();
                    toggle(check.id);
                  }
                }}
              >
                <div className="flex items-start gap-3 px-4 py-3">
                  <cfg.Icon className={`h-4 w-4 shrink-0 ${cfg.iconColor}`} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-text-primary">{check.title}</p>
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold ${cfg.badge}`}
                      >
                        {cfg.label}
                      </span>
                    </div>
                    {!isExpanded && check.status === "pass" ? (
                      <p className="mt-0.5 text-xs text-text-muted">{check.detail}</p>
                    ) : null}
                    {!isExpanded && check.status !== "pass" ? (
                      <p className="mt-0.5 text-xs text-text-muted">Click for details.</p>
                    ) : null}
                  </div>
                  {isClickable ? (
                    isExpanded ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-text-muted" aria-hidden />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-text-muted" aria-hidden />
                    )
                  ) : null}
                </div>
                {isExpanded ? (
                  <div
                    className={`border-t px-4 py-3 text-sm text-text-secondary ${
                      check.status === "fail"
                        ? "border-[#fca5a5] bg-[#fef3f2]"
                        : "border-[#fde68a] bg-[#fffaeb]"
                    }`}
                  >
                    {check.detail}
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>

      {hasWarnings && !hasBlockers && onAcknowledgeChange ? (
        <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-[#fde68a] bg-[#fffaeb] px-3 py-2">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => onAcknowledgeChange(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded"
          />
          <span className="text-sm text-[#92400e]">
            I have reviewed the warnings above and want to proceed.
          </span>
        </label>
      ) : null}

      {allPass ? null : null}
    </div>
  );
}

// Warnings are advisory - they surface information but do not block Approve.
// Only hard blockers (status="fail") prevent the workflow from proceeding.
export function preflightIsBlocked(checks = []) {
  return checks.some((c) => c.status === "fail");
}
