/** `size="sm"` is opt-in (tighter gap/padding, smaller text) for a narrower host like a side
 * panel; omitted, this renders exactly as it always did -- `ConfigureLayout.jsx`'s full-width
 * page tabs are unaffected either way. */
export default function Tabs({ tabs, activeTab, onChange, className = "", size = "default" }) {
  const sm = size === "sm";
  return (
    <div className={`flex items-center ${sm ? "gap-4" : "gap-6"} border-b border-border ${className}`} role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
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
