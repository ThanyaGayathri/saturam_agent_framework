import { useEffect, useMemo, useState } from "react";
import MiddlebyAutomationFlow from "../middleby/MiddlebyAutomationFlow";
import MiddlebyAutomationFlowAutomateStep from "../middleby/MiddlebyAutomationFlowAutomateStep";
import {
  getPlaybooksForCatalogId,
  profileIdForPlaybookId,
} from "../../domain/automation-registry";
import { MIDDLEBY_PROFILE_ID } from "../../domain/demo-profiles";

/**
 * Profile-aware Automate tab for the Insight Session.
 *
 * For Middleby catalog ids, delegates to MiddlebyAutomationFlow, which has
 * the full Analyze/Monitor/Automate journey (with monitor snapshots etc.).
 *
 * For retail/GFI catalog ids, renders just the Automate step directly, using
 * the same Prepare -> Review -> Approve -> Delivered pattern from the shared
 * automate step component. Retail/GFI do not have the full monitor
 * infrastructure, so this keeps the flow honest without inventing data.
 */
export default function InsightSessionAutomationPanel({
  catalogId,
  patterns,
  useCaseTitle,
  initialPatternId = null,
}) {
  const playbooks = useMemo(
    () => (catalogId ? getPlaybooksForCatalogId(catalogId) : []),
    [catalogId]
  );
  const visible = playbooks.filter((p) => patterns.some((pat) => pat.id === p.id));
  const [selectedId, setSelectedId] = useState(() => {
    if (initialPatternId && visible.some((p) => p.id === initialPatternId)) {
      return initialPatternId;
    }
    return visible[0]?.id ?? null;
  });

  useEffect(() => {
    if (
      initialPatternId &&
      visible.some((p) => p.id === initialPatternId) &&
      initialPatternId !== selectedId
    ) {
      setSelectedId(initialPatternId);
    }
  }, [initialPatternId, visible, selectedId]);

  const selected = visible.find((p) => p.id === selectedId) ?? visible[0] ?? null;

  if (!visible.length) {
    return (
      <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
        <p className="text-sm text-text-secondary">
          No follow-up workflows are available for this use case yet.
        </p>
      </section>
    );
  }

  const selectedProfileId = selected ? profileIdForPlaybookId(selected.id) : null;
  const isMiddleby = selectedProfileId === MIDDLEBY_PROFILE_ID;

  return (
    <div className="space-y-5">
      {visible.length > 1 ? (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Follow-up workflows for this use case
          </p>
          <p className="text-xs text-text-secondary">
            The same insight can trigger more than one type of follow-up. Choose which workflow to
            review.
          </p>
          <div className="flex flex-col gap-2">
            {visible.map((pb) => (
              <button
                key={pb.id}
                type="button"
                onClick={() => setSelectedId(pb.id)}
                className={`rounded-xl px-3 py-2.5 text-left ${
                  selected?.id === pb.id
                    ? "bg-primary text-white"
                    : "border border-border bg-surface-secondary text-text-secondary"
                }`}
              >
                <span className="text-xs font-semibold">{pb.label}</span>
                {pb.automationSummary ? (
                  <span
                    className={`mt-1 block text-[11px] leading-relaxed ${
                      selected?.id === pb.id ? "text-white/90" : "text-text-muted"
                    }`}
                  >
                    {pb.automationSummary}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        </div>
      ) : selected?.automationSummary ? (
        <p className="text-sm text-text-secondary">
          <span className="font-medium text-primary">Example workflow:</span>{" "}
          {selected.automationSummary}
        </p>
      ) : null}

      {selected ? (
        isMiddleby ? (
          <MiddlebyAutomationFlow
            patternId={selected.id}
            catalogId={catalogId}
            useCaseTitle={useCaseTitle}
            embedded
            entryPoint="insight"
          />
        ) : (
          <MiddlebyAutomationFlowAutomateStep
            patternId={selected.id}
            signalRecap={selected.trigger}
            hideStepOrdinal
            compactInsight
          />
        )
      ) : null}
    </div>
  );
}
