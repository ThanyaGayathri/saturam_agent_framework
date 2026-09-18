import { Check } from "lucide-react";

/**
 * One selectable "how do you want to make it?" card -- the picker both "make one" flows open
 * with (`create-dashboard/ModePickerStep.jsx` and `add-kpi/AddKpiModal.jsx`), so the two read
 * alike and a styling change is made once. Purely presentational: `selected` and `onClick` are
 * the caller's, the card only draws the state it is given.
 */
export default function ModeCard({ selected, onClick, Icon, title, children }) {
 return (
  <button
   type="button"
   onClick={onClick}
   className={`relative flex flex-col gap-2 rounded-lg border p-4 text-left transition-colors ${
    selected ? "border-primary bg-primary-light/40 ring-2 ring-primary/20" : "border-border bg-surface-default hover:border-primary/40"
   }`}
  >
   {selected && (
    <span className="absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-white">
     <Check size={12} strokeWidth={3} />
    </span>
   )}
   <span
    className={`flex h-9 w-9 items-center justify-center rounded-lg ${
     selected ? "bg-surface-default text-primary" : "bg-surface-tertiary text-text-secondary"
    }`}
   >
    <Icon size={18} />
   </span>
   <span className="text-sm font-semibold text-text-primary">{title}</span>
   <span className="text-xs leading-relaxed text-text-muted">{children}</span>
  </button>
 );
}

/** The text field / select styling the two modals' forms share (`ModePickerStep`'s name and
 * refresh fields, `AddKpiManualStep`'s whole form). No top margin of its own: each form adds
 * the gap it wants below its label. `ui/Input` / `ui/Select` exist but are styled differently,
 * so the constant is what is shared, not the component. */
export const FIELD_INPUT =
 "w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-60";
