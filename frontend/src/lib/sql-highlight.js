/**
 * A small, dependency-free SQL tokenizer for read-only syntax highlighting -- the KPI detail
 * panel's "Show SQL" block is the one place this repo displays a raw formula, and pulling in a
 * full syntax-highlighting library (Prism, highlight.js, a code editor) for one static code
 * block was judged disproportionate. Returns tokens, not HTML: the caller renders each as a
 * plain React text node (`{token.text}`), so there is no `dangerouslySetInnerHTML` and nothing
 * to escape -- React's own text-node handling is the whole safety story.
 *
 * Not a real SQL parser (no dialect awareness, no nested-comment handling) -- good enough to
 * color keywords/functions/strings/numbers/comments in the formulas this product's KPIs
 * actually use (`contracts/kpi.py`'s validated shape: one `SELECT` statement, `{named}`
 * placeholders for parameters), not a general-purpose highlighter.
 */

const KEYWORDS = new Set([
  "select", "from", "where", "and", "or", "not", "as", "on", "join", "left", "right", "inner",
  "outer", "full", "cross", "group", "by", "order", "having", "limit", "offset", "case", "when",
  "then", "else", "end", "distinct", "in", "is", "null", "like", "ilike", "between", "union",
  "all", "exists", "with", "asc", "desc", "over", "partition", "filter", "insert", "update",
  "delete", "into", "values", "set", "returning",
]);

const FUNCTIONS = new Set([
  "count", "sum", "avg", "min", "max", "round", "nullif", "coalesce", "cast", "extract",
  "date_trunc", "lower", "upper", "trim", "concat", "abs", "floor", "ceil", "now",
]);

//              comment      string                placeholder             number                  word          punctuation   whitespace  other
const TOKEN_RE = /(--[^\n]*)|('(?:[^']|'')*')|(\{[a-zA-Z_]\w*\})|(\b\d+(?:\.\d+)?\b)|([a-zA-Z_]\w*)|([(),.*=<>!+\-/;])|(\s+)|(.)/g;

/** `sql` -> `[{type, text}]`, `type` one of "comment"|"string"|"placeholder"|"number"|
 * "keyword"|"function"|"identifier"|"punctuation"|"whitespace". Concatenating every `text`
 * back together reproduces `sql` exactly -- no token is dropped or altered. */
export function tokenizeSql(sql) {
  const tokens = [];
  if (!sql) return tokens;
  for (const match of sql.matchAll(TOKEN_RE)) {
    const [full, comment, string, placeholder, number, word, punctuation, whitespace] = match;
    if (comment) tokens.push({ type: "comment", text: comment });
    else if (string) tokens.push({ type: "string", text: string });
    else if (placeholder) tokens.push({ type: "placeholder", text: placeholder });
    else if (number) tokens.push({ type: "number", text: number });
    else if (word) {
      const lower = word.toLowerCase();
      if (KEYWORDS.has(lower)) tokens.push({ type: "keyword", text: word });
      else if (FUNCTIONS.has(lower)) tokens.push({ type: "function", text: word });
      else tokens.push({ type: "identifier", text: word });
    } else if (punctuation) tokens.push({ type: "punctuation", text: punctuation });
    else if (whitespace) tokens.push({ type: "whitespace", text: whitespace });
    else tokens.push({ type: "identifier", text: full });
  }
  return tokens;
}

/** `tokenizeSql`'s output, split into per-line token arrays on `"\n"` inside whitespace tokens
 * -- for a line-numbered display. A `\n` that shares a whitespace token with other spaces (e.g.
 * `"\n  "` after re-indent) still splits correctly; the newline itself is never kept in a line's
 * own tokens. */
export function sqlLines(sql) {
  const lines = [[]];
  for (const token of tokenizeSql(sql)) {
    if (token.type !== "whitespace" || !token.text.includes("\n")) {
      lines[lines.length - 1].push(token);
      continue;
    }
    const parts = token.text.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ type: "whitespace", text: part });
    });
  }
  return lines;
}
