import { useState } from "react";
import Modal from "../../ui/Modal";
import AddKpiManualStep from "./AddKpiManualStep";

/**
 * "Add KPI" on the discovery page: a `KpiWrite` form that posts straight to the registry
 * (`onCreateManual`). The page owns everything that happens after -- the new registry row and its
 * panel; this modal only collects the input.
 *
 * KPIs the data supports are proposed for a connected source automatically, when its harvest
 * ends, so there is no "ask the model" path here any more: this is for a KPI you define yourself.
 *
 * `saving` lives here rather than in the form because the form cannot see what it needs to guard:
 * the manual step's `POST /kpis` in flight. `ui/Modal` calls `onClose` from its backdrop and X
 * unconditionally, and the page then strips `?add=1` and unmounts the whole modal -- so a dismiss
 * mid-save would drop a 422/409 on the floor (the form is the only place it is shown) or let a
 * "cancelled" KPI appear with its panel open. The guard is here, where `onClose` is forwarded.
 */
export default function AddKpiModal({
 areaState = { status: "ready", areas: [], error: null },
 onRetryAreas,
 onClose,
 onCreateManual,
}) {
 const [saving, setSaving] = useState(false);

 const close = () => {
  if (saving) return;
  onClose();
 };

 return (
  <Modal title="Add KPI" onClose={close} maxWidth="max-w-[760px]">
   <AddKpiManualStep
    areaState={areaState}
    onRetryAreas={onRetryAreas}
    saving={saving}
    onSavingChange={setSaving}
    onCreate={onCreateManual}
   />
  </Modal>
 );
}
