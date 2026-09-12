// Top-level renderer for an "answer-block" assistant message.
//
// Reads `msg.meta` and lays out:
//   1. Optional intro paragraph
//   2. Each block, dispatched through the registry
//   3. A CitationBlock derived from `meta.lineage` if not explicitly present
//   4. Follow-up prompt chips
//
// Renders nothing if the message is not an answer-block message  -  safe to
// mount alongside legacy rendering.

import { useMemo } from "react";
import { getBlockComponent } from "../../../domain/answer-blocks/registry";
import { isAnswerBlockMeta, BLOCK_TYPES } from "../../../domain/answer-blocks/schema";
import { CHAT_MODES } from "../../../storage/chat-mode";
import FollowUpChips from "./FollowUpChips";
import ReasoningPanel from "./ReasoningPanel";

// Display-side transform for "Quick summary" mode. Compresses the answer to
// the one-minute read  -  headline + a single findings tile + the "where this
// came from" citation. Everything else (charts, tables, journey, actions,
// follow-ups) is hidden until the user turns brief mode off.
function applyBriefMode(blocks) {
  if (!blocks?.length) return blocks || [];
  const brief = blocks.find((b) => b.type === BLOCK_TYPES.BRIEF);
  const insight = blocks.find((b) => b.type === BLOCK_TYPES.INSIGHT);
  const findings = blocks.find((b) => b.type === BLOCK_TYPES.FINDINGS);
  const citation = blocks.find((b) => b.type === BLOCK_TYPES.CITATION);

  const kept = [];
  if (brief) kept.push(brief);
  else if (insight) kept.push(insight);
  if (!brief && findings) kept.push(findings);
  if (citation) kept.push(citation);

  return kept.length ? kept : blocks.slice(0, 1);
}

function makeAutoCitation(lineage) {
  if (!lineage) return null;
  return {
    id: "citation-auto",
    type: BLOCK_TYPES.CITATION,
    sources: lineage.sources || [],
    ...(lineage.askForMiddleby ? { askForMiddleby: lineage.askForMiddleby } : {}),
  };
}

export default function AnswerBlockRenderer({ meta, chatMode, hideFollowUps = false, onFollowUpSelect, onAction, onPinToggle }) {
  const blocksToRender = useMemo(() => {
    if (!isAnswerBlockMeta(meta)) return [];
    let list = meta.blocks || [];
    const hasCitation = list.some((b) => b.type === BLOCK_TYPES.CITATION);
    if (!hasCitation && meta.lineage) {
      const auto = makeAutoCitation(meta.lineage);
      if (auto) list = [...list, auto];
    }
    if (chatMode === CHAT_MODES.BRIEF) {
      list = applyBriefMode(list);
    }
    return list;
  }, [meta, chatMode]);

  if (!isAnswerBlockMeta(meta)) return null;

  return (
    <div className="space-y-3">
      {meta.intro ? (
        <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-text-primary">
          {meta.intro}
        </p>
      ) : null}

      {meta.reasoning ? <ReasoningPanel reasoning={meta.reasoning} /> : null}

      {blocksToRender.map((block) => {
        const Component = getBlockComponent(block.type);
        if (!Component) {
          if (import.meta?.env?.DEV) {
            // eslint-disable-next-line no-console
            console.warn(`[answer-blocks] Unknown block type: ${block.type}`, block);
          }
          return null;
        }
        return (
          <Component
            key={block.id}
            block={block}
            onAction={onAction}
            onPinToggle={onPinToggle}
          />
        );
      })}

      {!hideFollowUps && meta.followUps?.length ? (
        <FollowUpChips prompts={meta.followUps} onSelect={onFollowUpSelect} />
      ) : null}
    </div>
  );
}
