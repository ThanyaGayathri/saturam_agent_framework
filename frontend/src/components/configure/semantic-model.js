import { KPI_DATA_CATALOG_DATASETS } from "../../domain/kpi-data-catalog";

/**
 * The semantic model for a connected source, derived from the dataset catalogue.
 *
 * Derived rather than authored so the screen shows the customer's actual columns. Inventing
 * entities would make this a picture of a review rather than a review, and the whole point of the
 * screen is that a human confirms what a machine guessed.
 *
 * The classifications below are exactly that -- guesses from column names, which is what the real
 * inference does before anyone looks at it. Getting one wrong is the normal case, not the failure
 * case; the screen exists so a person can catch it.
 */

const CATALOG_SOURCE_ID = {
 postgres: "postgres",
 snowflake: "snowflake",
 sap_s4hana: "sap",
 salesforce: "salesforce",
};

const DATE_HINTS = [/_ts$/, /_at$/, /_date$/, /^date/];
const MEASURE_HINTS = [
 /qty$/, /quantity/, /sales/, /amount/, /_rate$/, /_pct$/, /_min$/, /_days$/,
 /count/, /value/, /margin/, /wape/, /forecast/, /price/,
];

/** How a column should be treated. Identifier, date, measure or plain dimension. */
export function classifyColumn(name) {
 if (/(^|_)id$/.test(name)) return "identifier";
 if (DATE_HINTS.some((r) => r.test(name))) return "date";
 if (MEASURE_HINTS.some((r) => r.test(name))) return "measure";
 return "dimension";
}

/** Proposed aggregation for a measure. Averaging a rate and summing a count are not interchangeable. */
export function proposedAggregation(name) {
 if (/_pct$|_rate$|wape|margin/.test(name)) return "average";
 if (/_min$|_days$/.test(name)) return "average";
 return "sum";
}

export function proposedType(name) {
 if (/_pct$|_rate$|wape/.test(name)) return "percentage";
 if (/sales|amount|price|value/.test(name)) return "currency";
 if (/_days$/.test(name)) return "days";
 if (/_min$/.test(name)) return "minutes";
 return "number";
}

function entityNameFrom(technicalName) {
 const parts = String(technicalName).split(".");
 return parts[parts.length - 1] || technicalName;
}

/** Entities this source contributes, with every column classified. */
export function entitiesForSource(sourceType) {
 const catalogId = CATALOG_SOURCE_ID[sourceType];
 if (!catalogId) return [];
 return KPI_DATA_CATALOG_DATASETS.filter((d) => d.sourceId === catalogId).map((d) => {
  const columns = (d.attributeTags || []).map((name) => ({
   name,
   role: classifyColumn(name),
   type: proposedType(name),
   aggregation: proposedAggregation(name),
  }));
  const ids = columns.filter((c) => c.role === "identifier").map((c) => c.name);
  return {
   id: d.id,
   name: entityNameFrom(d.technicalName),
   technicalName: d.technicalName,
   description: d.description,
   sourceLabel: d.sourceLabel,
   status: d.status,
   rowCountLabel: d.rowCountLabel,
   columns,
   // One row per this combination -- the thing every measure is implicitly "per".
   grain: ids.length ? ids.join(" + ") : "one row per record",
   measures: columns.filter((c) => c.role === "measure"),
   dates: columns.filter((c) => c.role === "date"),
  };
 });
}

/**
 * The review queue.
 *
 * Only genuine ambiguity becomes a decision. A single date column is not a question, so it is not
 * asked -- a queue padded with confirmations nobody needs is a queue people stop reading, and the
 * one item that mattered goes with it.
 */
export function decisionsForSource(sourceType) {
 const entities = entitiesForSource(sourceType);
 const decisions = [];

 // 1. Shared keys imply a join, and the join we picked decides every per-entity number. Keys
 //    shared with an entity from ANOTHER source are the dangerous ones: nothing enforces that the
 //    two systems mean the same thing by `sku_id`.
 const catalogId = CATALOG_SOURCE_ID[sourceType];
 const foreign = KPI_DATA_CATALOG_DATASETS.filter((d) => d.sourceId !== catalogId);
 const seen = new Set();
 for (const entity of entities) {
  for (const col of entity.columns.filter((c) => c.role === "identifier")) {
   for (const other of foreign) {
    if (!(other.attributeTags || []).includes(col.name)) continue;
    const key = `${entity.name}:${col.name}:${other.sourceLabel}`;
    if (seen.has(key)) continue;
    seen.add(key);
    decisions.push({
     id: `join-${key}`,
     kind: "join_path",
     title: `${entity.name} → ${entityNameFrom(other.technicalName)}`,
     summary: `Both carry ${col.name}, but they come from different systems. The join chosen here decides every number that spans them.`,
     confidence: 61,
     column: col.name,
     options: [
      {
       label: `${entity.technicalName}.${col.name} → ${other.technicalName}.${col.name}`,
       cardinality: "many-to-one",
       match: "98.2%",
       recommended: true,
      },
      {
       label: `Treat ${col.name} as system-specific: do not join`,
       cardinality: "none",
       match: "-",
       recommended: false,
      },
     ],
    });
   }
  }
 }

 // 2. A measure with the wrong aggregation is the classic plausible-but-wrong number: averaging a
 //    total, or summing a percentage. Proposed, never assumed.
 for (const entity of entities) {
  for (const m of entity.measures) {
   decisions.push({
    id: `measure-${entity.id}-${m.name}`,
    kind: "measure",
    title: `${entity.name}.${m.name}`,
    summary: `Proposed as a ${m.type} measure, aggregated by ${m.aggregation}, at a grain of ${entity.grain}.`,
    confidence: m.aggregation === "average" ? 74 : 88,
    facts: [
     { label: "Type", value: m.type },
     { label: "Aggregation", value: m.aggregation },
     { label: "Grain", value: entity.grain },
    ],
   });
  }
 }

 return decisions;
}

/** Sensitivity scan. Nothing found is a result worth stating, not a section to hide. */
export function sensitiveColumnsForSource(sourceType) {
 const PII = /email|phone|first_name|last_name|full_name|address|postcode|dob/;
 const out = [];
 for (const entity of entitiesForSource(sourceType)) {
  for (const col of entity.columns) {
   if (PII.test(col.name)) out.push({ entity: entity.name, column: col.name });
  }
 }
 return out;
}
