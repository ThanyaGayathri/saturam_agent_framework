import { readLocalJson, writeLocalJson } from "../storage/local-json";
import { emptySpec } from "./use-case-spec";

/**
 * The working spec, carried across the builder steps.
 *
 * One object for the whole flow so each step reads what the last one wrote. Steps that hold their
 * own copy are how a wizard ends up publishing something the user never saw -- the title from
 * step 1 disagreeing with the question from step 2 is the classic version of that.
 *
 * localStorage, matching every other preference in this app. Nothing here talks to a backend.
 */

const KEY = "retailgpt:use-case-spec-draft";

/** Drafts saved before the shape was a field of its own carried it as an inferred autonomy. */
function migrate(raw) {
 const out = { ...raw };
 if (!out.shape && out.autonomy && (out.autonomyChosen || out.autonomyInferred)) {
  out.shape = out.autonomy;
  out.shapeChosen = true;
 }
 // Drafts from before categories existed: the category follows from the shape.
 if (out.shape && !out.category) {
  out.category = { inform: "analytics_automation", alert: "analytics_automation", recommend: "decision_intelligence", act: out.objective?.metric ? "process_optimization" : "process_automation" }[out.shape] || null;
 }
 if (typeof out.candidates === "string") out.candidates = out.candidates ? { concept: null, filter: out.candidates } : null;
 if (Array.isArray(out.guardrails)) {
  out.guardrails = out.guardrails.map((g) => (g && typeof g === "object" ? g : { rule: String(g), metric: null, operator: "gte", threshold: null, unit: "" }));
 }
 return out;
}

export function loadSpecDraft() {
 const raw = readLocalJson(KEY, null);
 if (!raw || typeof raw !== "object") return emptySpec();
 const spec = { ...emptySpec(), ...migrate(raw) };
 spec.autonomy = spec.shape || "inform";
 spec.autonomyChosen = !!spec.shape;
 return spec;
}

export function saveSpecDraft(spec) {
 writeLocalJson(KEY, spec);
 return spec;
}

export function clearSpecDraft() {
 writeLocalJson(KEY, null);
}
