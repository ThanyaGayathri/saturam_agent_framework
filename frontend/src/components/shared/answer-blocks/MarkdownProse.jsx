/**
 * Assistant prose, rendered as markdown.
 *
 * The playground's answers come from a model, and models write markdown — tables, bold, lists,
 * headings. Every surface in this app renders assistant text as a plain string with
 * `whitespace-pre-wrap`, which was fine while the replies were hand-authored fixtures and is
 * not fine now: a markdown table arrives as a wall of `| pipes | like | this |`.
 *
 * `remark-gfm` is what adds tables, strikethrough and task lists — plain CommonMark has no
 * table syntax, so without it the exact case that prompted this still renders as pipes.
 *
 * **No raw HTML.** `react-markdown` ignores embedded HTML unless `rehype-raw` is added, and it
 * is deliberately not added: this renders text a model produced from customer data, and an
 * `<img src=x onerror=...>` in that text must stay inert. Markdown syntax only.
 *
 * Styling is explicit per element rather than a typography plugin, because the app does not
 * have one and a block's prose has to sit inside `BlockShell` without fighting it.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const COMPONENTS = {
  p: ({ children }) => <p className="mt-1 text-sm text-text-secondary">{children}</p>,
  strong: ({ children }) => (
    <strong className="font-semibold text-text-primary">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => (
    <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-text-secondary">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-sm text-text-secondary">{children}</ol>
  ),
  li: ({ children }) => <li>{children}</li>,
  h1: ({ children }) => (
    <p className="mt-2 text-sm font-semibold text-text-primary">{children}</p>
  ),
  h2: ({ children }) => (
    <p className="mt-2 text-sm font-semibold text-text-primary">{children}</p>
  ),
  h3: ({ children }) => (
    <p className="mt-2 text-sm font-semibold text-text-primary">{children}</p>
  ),
  code: ({ inline, children }) =>
    inline ? (
      <code className="rounded bg-surface-secondary px-1 py-0.5 font-mono text-[13px]">
        {children}
      </code>
    ) : (
      <code className="block overflow-x-auto rounded bg-surface-secondary p-2 font-mono text-[13px]">
        {children}
      </code>
    ),
  // A wide table must scroll inside its own block rather than widening the chat column.
  table: ({ children }) => (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="border-b border-border">{children}</thead>,
  th: ({ children }) => (
    <th className="px-2 py-1 text-left font-medium text-text-primary">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border-b border-border/50 px-2 py-1 text-text-secondary">{children}</td>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer noopener" className="underline">
      {children}
    </a>
  ),
};

export default function MarkdownProse({ children }) {
  if (!children) return null;
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
      {String(children)}
    </ReactMarkdown>
  );
}
