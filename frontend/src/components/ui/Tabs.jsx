export default function Tabs({ tabs, activeTab, onChange, className = "" }) {
  return (
    <div className={`flex items-center gap-6 border-b border-border ${className}`}>
      {tabs.map((tab) => (
        <button
          key={tab.value}
          onClick={() => onChange(tab.value)}
          className={`pb-3 text-base font-semibold transition ${
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
