// KPI registry state now lives in a context provider so every consumer
// (KpiRegistry, KpiMappings, AddKpiPanel, KpiHealthDashboard, etc.) shares
// the same `data` reference and re-renders together when a KPI changes.
// The implementation moved to `../context/KpiRegistryContext.jsx`.
// This file is a thin re-export so existing `import { useKpiRegistry } from
// "../hooks/useKpiRegistry"` callers keep working.
export { useKpiRegistry } from "../context/KpiRegistryContext";
