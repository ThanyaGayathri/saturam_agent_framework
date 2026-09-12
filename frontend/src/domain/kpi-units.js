/** Unit pick lists by KPI level (demo - internal IDs stay out of the UI). */

const STRATEGIC = ["%", "$", "Score", "Ratio", "Index"];
const OPERATIONAL = ["%", "$", "Days", "Ratio", "Count", "Minutes", "Score"];
const TACTICAL = ["%", "Minutes", "Count", "Hours", "Score", "Days"];

const BY_LEVEL = {
  Strategic: STRATEGIC,
  Operational: OPERATIONAL,
  Tactical: TACTICAL,
};

export function unitOptionsForLevel(level) {
  const list = BY_LEVEL[level] || OPERATIONAL;
  return list.map((u) => ({ value: u, label: u }));
}

export function defaultUnitForLevel(level) {
  return unitOptionsForLevel(level)[0]?.value ?? "%";
}

export function isUnitValidForLevel(unit, level) {
  const vals = unitOptionsForLevel(level).map((o) => o.value);
  return vals.includes(unit);
}
