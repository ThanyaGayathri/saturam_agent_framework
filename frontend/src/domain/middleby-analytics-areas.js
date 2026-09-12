/** Business areas and subject areas for Middleby analytics KPIs and use case builder. */

// Middleby profile business areas.
// IDs are stable (KPI seeds reference them). Labels use standard business
// department names so the hierarchy reads as an org chart, not a set of
// analytics slice names.
export const MIDDLEBY_ANALYTICS_AREAS = [
  {
    id: "middleby-rfq",
    label: "Sales",
    subjectAreas: ["Pipeline", "Dealer follow-up"],
  },
  {
    id: "middleby-traffic",
    label: "Marketing",
    subjectAreas: ["Traffic", "Marketing channels"],
  },
  {
    id: "middleby-ux",
    label: "Customer Experience",
    subjectAreas: ["Error tracking", "Session quality"],
  },
  {
    id: "middleby-demand",
    label: "Merchandise",
    subjectAreas: ["Category demand", "Product discovery"],
  },
  {
    id: "middleby-executive",
    label: "Finance",
    subjectAreas: ["Portfolio performance", "Weekly signals"],
  },
];

export function middlebyBusinessAreaFormOptions() {
  return MIDDLEBY_ANALYTICS_AREAS.map((a) => ({ value: a.id, label: a.label }));
}

export function middlebySubjectAreaFormOptions(businessAreaId) {
  const area = MIDDLEBY_ANALYTICS_AREAS.find((a) => a.id === businessAreaId);
  const subjects = area?.subjectAreas?.length ? area.subjectAreas : ["Analytics"];
  return subjects.map((s) => ({ value: s, label: s }));
}

export function inferMiddlebyBusinessAreaIdForSubject(subjectArea) {
  const needle = String(subjectArea || "").trim().toLowerCase();
  if (!needle) return MIDDLEBY_ANALYTICS_AREAS[0]?.id ?? "middleby-rfq";
  for (const area of MIDDLEBY_ANALYTICS_AREAS) {
    if (area.subjectAreas.some((s) => s.toLowerCase() === needle)) return area.id;
  }
  return MIDDLEBY_ANALYTICS_AREAS[0]?.id ?? "middleby-rfq";
}

export function middlebyBusinessAreaLabel(businessAreaId) {
  return MIDDLEBY_ANALYTICS_AREAS.find((a) => a.id === businessAreaId)?.label ?? businessAreaId;
}
