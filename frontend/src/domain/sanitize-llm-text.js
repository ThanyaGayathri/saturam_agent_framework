/**
 * Strip markdown-style decorations so assistant text renders as plain, clean sentences in the demo UI.
 *
 * The chat bubble is a single <p className="whitespace-pre-wrap">, so characters like **bold** or ###
 * would otherwise show up literally. We keep line breaks and bullet markers, but normalize them.
 */

const MAX_BLANK_LINES = 2;

export function sanitizeLlmText(input) {
  if (input == null) return "";
  let s = String(input);

  s = s.replace(/^\s*```[a-z0-9_-]*\s*\n?/i, "");
  s = s.replace(/\n?```\s*$/i, "");
  s = s.replace(/```/g, "");

  s = s.replace(/\*\*\*([^\n*]+)\*\*\*/g, "$1");
  s = s.replace(/\*\*([^\n*]+)\*\*/g, "$1");
  s = s.replace(/__([^\n_]+)__/g, "$1");

  s = s.replace(/(^|[\s(])\*([^*\n]{1,200})\*(?=[\s).,;:!?]|$)/g, "$1$2");
  s = s.replace(/(^|[\s(])_([^_\n]{1,200})_(?=[\s).,;:!?]|$)/g, "$1$2");

  s = s.replace(/`([^`\n]+)`/g, "$1");

  s = s.replace(/^\s{0,3}#{1,6}\s+/gm, "");

  s = s.replace(/^\s*[-*+]\s+/gm, "• ");

  s = s.replace(/[ \t]+\n/g, "\n");
  s = s.replace(new RegExp(`\\n{${MAX_BLANK_LINES + 2},}`, "g"), "\n".repeat(MAX_BLANK_LINES + 1));

  return s.trim();
}
