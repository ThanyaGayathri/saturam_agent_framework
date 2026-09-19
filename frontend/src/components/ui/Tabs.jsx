/** `size="sm"` is opt-in (tighter gap/padding, smaller text) for a narrower host like a side
 * panel; omitted, this renders exactly as it always did -- `ConfigureLayout.jsx`'s full-width
 * page tabs are unaffected either way.
 *
 * Deliberately NOT `role="tablist"`/`role="tab"`: that ARIA pattern also requires a paired
 * `role="tabpanel"` (`aria-controls`/`aria-labelledby`) and arrow-key/roving-tabindex handling,
 * none of which either caller wires up -- `ConfigureLayout.jsx`'s "tabs" navigate to different
 * routes entirely (closer to a nav landmark than a same-page tab widget) and `KpiDetailPanel.jsx`
 * switches sibling content in place but doesn't id-pair it. A screen reader announcing a tab
 * widget whose expected keyboard interaction doesn't work is worse than plain buttons -- so
 * this stays plain buttons, `aria-selected` only as a lightweight, non-widget state hint. */
export default function Tabs({ tabs, activeTab, onChange, className = "", size = "default" }) {
  const sm = size === "sm";
  return (
    <div className={`flex items-center ${sm ? "gap-4" : "gap-6"} border-b border-border ${className}`}>
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          aria-selected={activeTab === tab.value}
          onClick={() => onChange(tab.value)}
          className={`${sm ? "pb-2.5 text-sm" : "pb-3 text-base"} font-semibold transition ${
            activeTab === tab.value
              ? "border-b-2 border-primary text-primary"
              : "text-text-muted hover:text-text-tertiary"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
