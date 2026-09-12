// Registry mapping block `type` strings to their React components.
//
// Adding a new block type: add the constant to schema.js, create the component,
// then register it here. The renderer looks up by type at runtime — no other
// wiring needed.

import InsightBlock from "../../components/shared/answer-blocks/blocks/InsightBlock";
import FindingsBlock from "../../components/shared/answer-blocks/blocks/FindingsBlock";
import ChartBlock from "../../components/shared/answer-blocks/blocks/ChartBlock";
import TableBlock from "../../components/shared/answer-blocks/blocks/TableBlock";
import RecommendationBlock from "../../components/shared/answer-blocks/blocks/RecommendationBlock";
import CitationBlock from "../../components/shared/answer-blocks/blocks/CitationBlock";
import ActionBlock from "../../components/shared/answer-blocks/blocks/ActionBlock";
import BriefBlock from "../../components/shared/answer-blocks/blocks/BriefBlock";
import JourneyBlock from "../../components/shared/answer-blocks/blocks/JourneyBlock";
import RegionMapBlock from "../../components/shared/answer-blocks/blocks/RegionMapBlock";
import RcaBlock from "../../components/shared/answer-blocks/blocks/RcaBlock";
import { BLOCK_TYPES } from "./schema";

export const BLOCK_COMPONENTS = Object.freeze({
  [BLOCK_TYPES.INSIGHT]: InsightBlock,
  [BLOCK_TYPES.FINDINGS]: FindingsBlock,
  [BLOCK_TYPES.CHART]: ChartBlock,
  [BLOCK_TYPES.TABLE]: TableBlock,
  [BLOCK_TYPES.RECOMMENDATION]: RecommendationBlock,
  [BLOCK_TYPES.CITATION]: CitationBlock,
  [BLOCK_TYPES.ACTION]: ActionBlock,
  [BLOCK_TYPES.BRIEF]: BriefBlock,
  [BLOCK_TYPES.JOURNEY]: JourneyBlock,
  [BLOCK_TYPES.REGION_MAP]: RegionMapBlock,
  [BLOCK_TYPES.RCA]: RcaBlock,
});

export function getBlockComponent(type) {
  return BLOCK_COMPONENTS[type] || null;
}
