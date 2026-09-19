/**
 * The playground, against the real backend.
 *
 * Two surfaces, not one, and the split is the backend's rather than ours:
 *
 *   - **Chat is a framework endpoint.** `POST /agents/{id}/runs`, multipart form, with the
 *     mode carried in `factory_input`. R11 is an `AgentFactory`: EgptOS validates that JSON
 *     against `PlaygroundModeInput` *before* the factory builds an agent, so an invalid mode
 *     is a 400 rather than a wrong answer. R11 deliberately never touches chat transport.
 *   - **Everything else is the retail router** — saved prompts, filters, preferences, reports
 *     and `/runs/{id}/provenance` — reached through `lib/api.js` like every other wired screen.
 *
 * `blocksFromAnswer` is the one piece of translation, and it is deliberately thin. The backend
 * emits six groundable kinds; this app's renderer has eleven richer types. They are not the
 * same vocabulary and should not be forced into one: the backend's set is what a tool result
 * can actually fill, and the extra frontend types (journey, rca, regionMap, action) need data
 * no executor produces. Mapping down is honest; extending the backend's enum up would hand a
 * model a vocabulary it can only fill by inventing.
 */

import { apiForm, apiGet } from "../lib/api.js";
import { BLOCK_TYPES } from "../domain/answer-blocks/schema.js";

const AGENT_ID = "playground";

/** The four grounding scopes `resolve_mode_scope` accepts. Mirrors `PlaygroundMode`. */
export const PLAYGROUND_MODES = Object.freeze({
  DATA: "data",
  KNOWLEDGE: "knowledge",
  USE_CASE: "use_case",
  BUSINESS_AREA: "business_area",
});

/**
 * Backend `AnswerBlockKind` -> a block the renderer can actually draw.
 *
 * **Each target type has its own required shape, and a block missing it renders nothing.**
 * `FindingsBlock` and `RecommendationBlock` both open with `if (!items.length) return null`;
 * `TableBlock` needs `columns`/`rows`; `ChartBlock` needs `data`. An earlier version of this
 * file emitted a uniform `{headline, subtext, content, source}` for every kind, so five of the
 * six backend kinds produced a blank bubble — the answer arrived intact from the backend and
 * the component dropped it on the floor. Hence one builder per target rather than one mapping
 * table: the shape is the contract, not the type name.
 *
 * The backend's six kinds are prose-and-numbers; the renderer's richer types (journey, rca,
 * regionMap) need data no executor produces, so nothing maps onto them.
 */

/** `text` -> InsightBlock: `headline` + `subtext`, the renderer's prose block. */
function asInsight(block, body) {
  return {
    id: block.id,
    type: BLOCK_TYPES.INSIGHT,
    kind: block.kind,
    headline: block.title || "",
    subtext: body,
  };
}

/**
 * `finding` / `kpi` -> FindingsBlock, whose `items` are `{title, value, change,
 * changeDirection, sentiment, desc}` stat tiles.
 *
 * The backend may now send `items` directly (one block, several tiles — the "At a glance"
 * row). When it does not, one block still becomes one tile carrying the prose, which is what
 * every pre-structured response produces: `value` takes the text and `desc` the source
 * attribution. Parsing a number back out of the prose is deliberately not attempted — that is
 * the guessing the rest of this pipeline refuses to do.
 */
function asFindings(block, body) {
  const items = Array.isArray(block.items) && block.items.length
    ? block.items.map((item) => ({
        title: item.title ?? "",
        value: item.value ?? "",
        desc: item.desc ?? "",
        change: item.change ?? undefined,
        changeDirection: item.change_direction ?? item.changeDirection ?? undefined,
        sentiment: item.sentiment ?? undefined,
      }))
    : [{ title: block.title || "", value: body, desc: block.source || "" }];
  return {
    id: block.id,
    type: BLOCK_TYPES.FINDINGS,
    kind: block.kind,
    title: block.title || "",
    items,
  };
}

/**
 * `recommendation` -> RecommendationBlock, whose items are `{text, priority, rationale}`.
 *
 * `priority` drives the "Do this first" / "Worth doing" / "Nice to have" badge, so a backend
 * that sends it gets the graded list; one that does not still gets a single readable item.
 */
function asRecommendation(block, body) {
  const items = Array.isArray(block.items) && block.items.length
    ? block.items.map((item) =>
        typeof item === "string"
          ? { text: item }
          : { text: item.text ?? "", priority: item.priority ?? undefined, rationale: item.rationale ?? undefined },
      )
    : [{ text: body }];
  return {
    id: block.id,
    type: BLOCK_TYPES.RECOMMENDATION,
    kind: block.kind,
    title: block.title || "",
    items,
  };
}

/**
 * A one-line caption, or nothing. Guards against the rare structured-output failure where a
 * low-priority string field like this one comes back as several hundred characters of
 * degenerate, self-referential text ("...placeholder_do_not_use_this_key...") ending in a
 * stray fragment of JSON syntax -- observed directly, not a hypothetical. `data`/`items`/etc.
 * are validated by the backend's own schema and can't carry this; a free-text field can, so
 * it is the one place the frontend checks before rendering.
 */
function sanitizedCaption(text) {
  if (typeof text !== "string") return "";
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > 220 || /[{}[\]]/.test(trimmed)) return "";
  return trimmed;
}

/**
 * `chart` -> ChartBlock when the backend sent series data, prose otherwise.
 *
 * `chart_kind` is the model's answer to "draw this as a pie/line/bar", and ChartBlock already
 * supports `spark|bar|line|funnel|pie|sankey|treemap`; an unknown kind falls back to its own
 * tabular view rather than vanishing. The snake_case names are the backend's idiom and are
 * mapped here rather than at the source, so neither side has to speak the other's convention.
 *
 * Falling back to Insight when `data` is empty is the same judgement the previous version
 * made for every chart: an empty ChartBlock reads as "there is no data" rather than "this was
 * not drawn", and the prose at least carries the answer.
 */
function asChart(block, body) {
  const data = Array.isArray(block.data) ? block.data : [];
  if (!data.length) return asInsight(block, body);
  return {
    id: block.id,
    type: BLOCK_TYPES.CHART,
    kind: block.kind,
    title: block.title || "",
    description: sanitizedCaption(block.description) || body,
    chartKind: block.chart_kind ?? block.chartKind ?? "bar",
    data,
    config: {
      xKey: block.x_key ?? block.xKey ?? "label",
      yKey: block.y_key ?? block.yKey ?? "value",
      ...(block.config || {}),
    },
  };
}

/**
 * `table` -> TableBlock when the backend sent `columns` and `rows`, prose otherwise.
 *
 * Rows arrive as a matrix — cell values in column order — because the backend's schema runs
 * under `additionalProperties: false`, which forbids the arbitrary per-row keys an object
 * would need. They are zipped back against `columns` here, since `TableBlock` reads
 * `row[column.key]`. A row shorter than the header yields blanks rather than dropping the row.
 */
function asTable(block, body) {
  const columns = Array.isArray(block.columns) ? block.columns : [];
  const rawRows = Array.isArray(block.rows) ? block.rows : [];
  if (!columns.length || !rawRows.length) return asInsight(block, body);
  const rows = rawRows.map((row) =>
    Array.isArray(row)
      ? columns.reduce((acc, column, i) => {
          acc[column.key || `col_${i}`] = row[i] ?? "";
          return acc;
        }, {})
      : row,
  );
  return {
    id: block.id,
    type: BLOCK_TYPES.TABLE,
    kind: block.kind,
    title: block.title || "",
    columns,
    rows,
    ...(block.max_rows ?? block.maxRows ? { maxRows: block.max_rows ?? block.maxRows } : {}),
  };
}

const BUILDERS = Object.freeze({
  text: asInsight,
  finding: asFindings,
  kpi: asFindings,
  recommendation: asRecommendation,
  table: asTable,
  chart: asChart,
});

/**
 * One backend block as a renderable one.
 *
 * `content` is what `playground.md` asks the model for; `text` is accepted as a fallback
 * because the contract leaves the inner shape open. An unrecognised kind renders as prose
 * rather than vanishing — a block the backend added and this file has not caught up with
 * should still show its content.
 */
function toRenderableBlock(block) {
  const body = block.content ?? block.text ?? "";
  const build = BUILDERS[block.kind] || asInsight;
  return build(block, body);
}

/**
 * A `PlaygroundAnswer` as `AnswerBlockRenderer` meta.
 *
 * `lineage` is left for the caller to fill from the real provenance record rather than being
 * derived from the answer: the renderer builds its citation block from `meta.lineage`, and a
 * model citing itself is exactly what R7's provenance row exists to replace.
 */
export function blocksFromAnswer(answer, { lineage = null } = {}) {
  const blocks = Array.isArray(answer?.blocks) ? answer.blocks : [];
  // `followUps` is meta, not a block: `AnswerBlockRenderer` draws it as chips below the
  // blocks and only when this is the latest assistant message, so it must not become part of
  // the block list. Filtered to non-empty strings because a chip with no prompt behind it
  // still looks clickable.
  const followUps = (Array.isArray(answer?.follow_ups) ? answer.follow_ups : answer?.followUps || [])
    .filter((prompt) => typeof prompt === "string" && prompt.trim())
    .slice(0, 3);
  return {
    variant: "answer-block",
    blocks: blocks.map(toRenderableBlock),
    ...(answer?.notes ? { intro: answer.notes } : {}),
    ...(lineage ? { lineage } : {}),
    ...(followUps.length ? { followUps } : {}),
  };
}

/**
 * Ask the playground agent one question.
 *
 * `stream: false` on purpose for now: the renderer draws whole blocks, so there is nothing to
 * paint incrementally until a block-level streaming shape exists. Swapping to SSE later is a
 * change to this function and to nothing that calls it.
 */
export async function askPlayground(
  { question, mode = PLAYGROUND_MODES.DATA, scopeId = null, sessionId = null, signal } = {},
) {
  const form = new FormData();
  form.append("message", question);
  form.append("factory_input", JSON.stringify({ mode, scope_id: scopeId }));
  form.append("stream", "false");
  if (sessionId) form.append("session_id", sessionId);

  const run = await apiForm(`/agents/${AGENT_ID}/runs`, "POST", form, { signal });

  // A refused mode still returns 200 with an empty answer and the reason in `notes` --
  // `resolve_mode_scope` declines before an agent is built, which is a refusal rather than a
  // failure and must not be rendered as an error.
  const answer = run?.content ?? {};
  return {
    runId: run?.run_id ?? null,
    sessionId: run?.session_id ?? null,
    status: run?.status ?? "UNKNOWN",
    answer,
    refused: Array.isArray(answer.blocks) && answer.blocks.length === 0 && !!answer.notes,
  };
}

/** Where every number in an answer came from (R7). Null when the run wrote no record. */
export async function fetchProvenance(runId, { signal } = {}) {
  if (!runId) return null;
  try {
    return await apiGet(`/runs/${runId}/provenance`, { signal });
  } catch {
    // A missing provenance row is not an error the user can act on: the answer still stands,
    // it just cannot show its sources. Returning null lets the renderer omit the citation
    // block rather than fail the whole message.
    return null;
  }
}

/**
 * A provenance record as the renderer's `lineage`.
 *
 * `CitationBlock`'s `SourceRow` reads `{system, scope, isReal, note}`. An earlier version
 * emitted `{label, kind}`, so every row rendered blank **and**, because `isReal` was
 * `undefined`, carried the "Example data — Middleby data would replace this" badge. A real
 * provenance row was therefore displayed as fabricated, which is the exact inversion of what
 * R7 exists to prove.
 *
 * `isReal: true` is correct by construction here: a provenance row is only written by a run
 * that actually read something, so anything reaching this function came from real data.
 *
 * The record's `datasets_read` entries are `{schema, table, as_of}` (`DatasetRead` in the
 * generated API models) — there is no `name` and no `dataset`, which an earlier `d.name ||
 * d.dataset || "dataset"` fell through to, labelling every source with the literal word
 * "dataset".
 */
export function lineageFromProvenance(record) {
  if (!record) return null;
  const sources = [
    ...(record.datasets_read || []).map((d) => ({
      system: typeof d === "string" ? d : [d.schema, d.table].filter(Boolean).join("."),
      scope: d?.as_of ? `as of ${d.as_of}` : "",
      isReal: true,
      note: "",
    })),
    ...(record.sql_executed || []).map((q) => ({
      system: q?.table || q?.dataset || "warehouse",
      scope: q?.rows != null ? `${q.rows} row(s)` : "",
      isReal: true,
      note: q?.statement ? String(q.statement).slice(0, 120) : "",
    })),
    ...(record.retrieval_hits || []).map((h) => ({
      system: h?.origin || "retrieval",
      scope: h?.id ? String(h.id) : "",
      isReal: true,
      note: h?.score != null ? `score ${h.score}` : "",
    })),
  ].filter((s) => s.system);
  if (!sources.length) return null;
  return { sources, modelTier: record.model_tier || null };
}

export const playgroundApi = {
  ask: askPlayground,
  provenance: fetchProvenance,
  lineageFromProvenance,
  blocksFromAnswer,
  MODES: PLAYGROUND_MODES,
};
