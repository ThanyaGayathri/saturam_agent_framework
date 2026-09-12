function userMessageHasHandoffMeta(meta) {
  return !!(meta?.catalogId || meta?.source === "builder-catalog" || meta?.source === "playground-hub");
}

/** Hide back-to-back duplicate user bubbles (e.g. double seed / stale IndexedDB); never drop catalog/hub turns. */
export function collapseAdjacentDuplicateUserMessages(rows) {
  if (!rows?.length) return rows;
  const out = [];
  for (const m of rows) {
    const last = out[out.length - 1];
    if (
      last?.role === "user" &&
      m.role === "user" &&
      last.text === m.text &&
      last.meta?.horizon === m.meta?.horizon &&
      last.meta?.lens === m.meta?.lens &&
      last.meta?.catalogId === m.meta?.catalogId &&
      !userMessageHasHandoffMeta(last.meta) &&
      !userMessageHasHandoffMeta(m.meta)
    )
      continue;
    out.push(m);
  }
  return out;
}
