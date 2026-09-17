/**
 * Picks which of R11's four grounding scopes the next question runs in.
 *
 * Without this control the stored mode never changed, so `knowledge`, `use_case` and
 * `business_area` were unreachable from the product no matter what the backend supported —
 * every turn went to `data`. The storage module existed and nothing called its writer.
 *
 * Two of the modes need a `scope_id` and the backend refuses them without one
 * (`resolve_mode_scope` declines before an agent is built). Rather than let a user send a
 * request that is guaranteed to be refused, the picker asks for the scope inline and keeps the
 * mode unselected until it has one.
 */

import { useEffect, useState } from "react";
import {
  MODES_NEEDING_SCOPE,
  PLAYGROUND_MODES,
  readPlaygroundMode,
  writePlaygroundMode,
} from "../../storage/playground-mode";

const LABELS = Object.freeze({
  [PLAYGROUND_MODES.DATA]: "Data",
  [PLAYGROUND_MODES.KNOWLEDGE]: "Knowledge base",
  [PLAYGROUND_MODES.USE_CASE]: "Use case",
  [PLAYGROUND_MODES.BUSINESS_AREA]: "Business area",
});

const HINTS = Object.freeze({
  [PLAYGROUND_MODES.DATA]: "KPI values from gold, and guarded SQL over silver",
  [PLAYGROUND_MODES.KNOWLEDGE]: "Domain prose, the ontology and this deployment's semantic layer",
  [PLAYGROUND_MODES.USE_CASE]: "One use case's runs, found by reference",
  [PLAYGROUND_MODES.BUSINESS_AREA]: "One business area's concepts, and nothing outside it",
});

const SCOPE_PLACEHOLDER = Object.freeze({
  [PLAYGROUND_MODES.USE_CASE]: "use case id",
  [PLAYGROUND_MODES.BUSINESS_AREA]: "business area, e.g. inventory",
});

export default function PlaygroundModePicker({ profileId, onChange }) {
  const [{ mode, scopeId }, setState] = useState(() => readPlaygroundMode(profileId));
  const [draftScope, setDraftScope] = useState(scopeId || "");

  useEffect(() => {
    const next = readPlaygroundMode(profileId);
    setState(next);
    setDraftScope(next.scopeId || "");
  }, [profileId]);

  const needsScope = MODES_NEEDING_SCOPE.includes(mode);

  function apply(nextMode, nextScope) {
    // Writing clears the stored session whenever mode or scope changes -- continuing a backend
    // session across a tool-set change hands the model a history it can no longer act on.
    const saved = writePlaygroundMode(profileId, { mode: nextMode, scopeId: nextScope || null });
    setState({ mode: saved.mode, scopeId: saved.scopeId });
    if (onChange) onChange(saved);
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-text-muted">Grounded in:</span>
      {Object.values(PLAYGROUND_MODES).map((m) => (
        <button
          key={m}
          type="button"
          title={HINTS[m]}
          onClick={() => apply(m, MODES_NEEDING_SCOPE.includes(m) ? draftScope : null)}
          className={`rounded-full border px-3 py-1 transition ${
            mode === m
              ? "border-transparent bg-text-primary text-surface-primary"
              : "border-border text-text-muted hover:text-text-primary"
          }`}
        >
          {LABELS[m]}
        </button>
      ))}
      {needsScope ? (
        <input
          value={draftScope}
          onChange={(e) => setDraftScope(e.target.value)}
          onBlur={() => apply(mode, draftScope)}
          onKeyDown={(e) => {
            if (e.key === "Enter") apply(mode, draftScope);
          }}
          placeholder={SCOPE_PLACEHOLDER[mode]}
          aria-label={SCOPE_PLACEHOLDER[mode]}
          className="rounded-md border border-border bg-surface-primary px-2 py-1 text-text-primary"
        />
      ) : null}
      {needsScope && !scopeId ? (
        <span className="text-text-muted">
          {LABELS[mode]} needs a {SCOPE_PLACEHOLDER[mode]} — the backend refuses it without one.
        </span>
      ) : null}
    </div>
  );
}
