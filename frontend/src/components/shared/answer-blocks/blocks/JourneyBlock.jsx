// Interactive customer journey timeline.
//
// Uses @xyflow/react to render events across 4 lanes (GA4, LogRocket, HubSpot,
// Salesforce). Nodes are clickable and expose event details in a side panel.
// The lane structure is data-driven  -  any lane name from the schema shows up
// as its own row.

import { useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertTriangle, Circle, Star, X, ExternalLink } from "lucide-react";
import BlockShell from "../BlockShell";

const LANE_COLORS = {
  GA4: { bg: "#FEF3C7", ring: "#F59E0B", text: "#B45309" },
  LogRocket: { bg: "#FCE7F3", ring: "#EC4899", text: "#9D174D" },
  HubSpot: { bg: "#FFE4E1", ring: "#F97316", text: "#9A3412" },
  Salesforce: { bg: "#DBEAFE", ring: "#0EA5E9", text: "#075985" },
  "Adobe Commerce": { bg: "#FEE2E2", ring: "#EF4444", text: "#991B1B" },
  Akeneo: { bg: "#D1FAE5", ring: "#10B981", text: "#065F46" },
};

function laneStyle(lane) {
  return LANE_COLORS[lane] || { bg: "#F3F4F6", ring: "#6B7280", text: "#374151" };
}

const KIND_ICONS = {
  issue: AlertTriangle,
  milestone: Star,
  info: Circle,
};

function formatShortTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// Custom node component  -  coloured pill with an icon + short label.
function JourneyEventNode({ data }) {
  const style = laneStyle(data.lane);
  const kind = data.kind || "info";
  const Icon = KIND_ICONS[kind] || Circle;
  return (
    <div
      className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium shadow-sm"
      style={{
        borderColor: style.ring,
        backgroundColor: style.bg,
        color: style.text,
        maxWidth: 220,
      }}
      title={data.label}
    >
      <Icon size={11} />
      <span className="truncate">{data.label}</span>
      <Handle type="target" position={Position.Left} style={{ opacity: 0, pointerEvents: "none" }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0, pointerEvents: "none" }} />
    </div>
  );
}

const NODE_TYPES = { journeyEvent: JourneyEventNode };

function useJourneyGraph(block) {
  return useMemo(() => {
    const lanes = block.lanes || [];
    const events = (block.events || []).map((e, i) => ({ ...e, __idx: i }));

    const NODE_WIDTH = 220;
    const NODE_GAP_X = 260;
    const LANE_GAP_Y = 88;
    const LEFT_INSET = 220;
    const TOP_INSET = 32;

    // Sort events chronologically then assign x by index.
    const sortedByTime = [...events].sort((a, b) => new Date(a.at) - new Date(b.at));

    // Position each event: x by chronological order, y by lane.
    const timeIndex = new Map();
    sortedByTime.forEach((e, i) => timeIndex.set(e.__idx, i));

    const nodes = events.map((e) => {
      const laneIdx = Math.max(0, lanes.indexOf(e.lane));
      const x = LEFT_INSET + (timeIndex.get(e.__idx) ?? 0) * NODE_GAP_X;
      const y = TOP_INSET + laneIdx * LANE_GAP_Y;
      return {
        id: `ev-${e.__idx}`,
        type: "journeyEvent",
        position: { x, y },
        data: { label: e.label, lane: e.lane, kind: e.kind, at: e.at, detail: e.detail, href: e.href, raw: e },
        draggable: false,
        selectable: true,
      };
    });

    // Sort ↔ connect chronologically-adjacent events with edges.
    const edges = [];
    for (let i = 0; i < sortedByTime.length - 1; i += 1) {
      const from = sortedByTime[i];
      const to = sortedByTime[i + 1];
      edges.push({
        id: `e-${from.__idx}-${to.__idx}`,
        source: `ev-${from.__idx}`,
        target: `ev-${to.__idx}`,
        type: "smoothstep",
        animated: false,
        style: { stroke: "#9CA3AF", strokeWidth: 1.5 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#9CA3AF" },
      });
    }

    const height = TOP_INSET + Math.max(1, lanes.length) * LANE_GAP_Y + 20;
    const width = LEFT_INSET + Math.max(2, events.length) * NODE_GAP_X;

    return { nodes, edges, lanes, height, width };
  }, [block]);
}

function LaneBackground({ lanes, height }) {
  const LANE_GAP_Y = 88;
  const TOP_INSET = 32;
  const LANE_LABEL_WIDTH = 200;
  return (
    <div className="absolute inset-0 pointer-events-none" style={{ height }}>
      {/* Vertical divider so lane labels feel separate from the timeline */}
      <div
        className="absolute top-0 h-full border-r border-dashed border-border"
        style={{ left: LANE_LABEL_WIDTH - 20 }}
      />
      {lanes.map((lane, i) => {
        const style = laneStyle(lane);
        return (
          <div
            key={lane}
            className="absolute flex items-center gap-2 text-[12px] font-semibold"
            style={{
              top: TOP_INSET + i * LANE_GAP_Y + 4,
              left: 0,
              width: LANE_LABEL_WIDTH,
              paddingLeft: 12,
              color: style.text,
            }}
          >
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: style.ring }}
              aria-hidden
            />
            <span className="truncate">{lane}</span>
          </div>
        );
      })}
    </div>
  );
}

function EventDetailPanel({ event, onClose }) {
  if (!event) return null;
  const style = laneStyle(event.lane);
  return (
    <div className="mt-3 rounded-xl border border-border bg-surface-secondary/60 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1"
              style={{ backgroundColor: style.bg, color: style.text, borderColor: style.ring }}
            >
              {event.lane}
            </span>
            {event.kind === "issue" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-status-critical/10 px-2 py-0.5 text-[10px] font-medium text-status-critical">
                UX issue
              </span>
            ) : null}
            {event.kind === "milestone" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-status-success/10 px-2 py-0.5 text-[10px] font-medium text-status-success">
                Milestone
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm font-semibold text-text-primary">{event.label}</p>
          <p className="mt-0.5 text-[11px] text-text-muted">{formatShortTime(event.at)}</p>
          {event.detail ? (
            <pre className="mt-2 whitespace-pre-wrap rounded bg-surface-default p-2 text-[11px] text-text-secondary">
              {typeof event.detail === "string" ? event.detail : JSON.stringify(event.detail, null, 2)}
            </pre>
          ) : null}
          {event.href ? (
            <a
              href={event.href}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
            >
              Open in {event.lane} <ExternalLink size={10} />
            </a>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close event details"
          className="rounded p-1 text-text-muted hover:bg-surface-default"
        >
          <X size={12} />
        </button>
      </div>
    </div>
  );
}

export default function JourneyBlock({ block, onPinToggle }) {
  const [selected, setSelected] = useState(null);
  const { nodes, edges, lanes, height } = useJourneyGraph(block);

  const handleNodeClick = (_, node) => {
    setSelected(node.data.raw);
  };

  const totalEvents = (block.events || []).length;
  const laneCount = lanes.length;
  const customerLine = block.customerLabel
    || (block.customerId ? `Example customer • ${block.customerId}` : "Example customer");

  return (
    <BlockShell
      block={block}
      title={block.title || "How one customer moved across our systems"}
      subtitle={`${customerLine} • ${totalEvents} step${totalEvents === 1 ? "" : "s"} across ${laneCount} system${laneCount === 1 ? "" : "s"}`}
      onPinToggle={onPinToggle}
    >
      <p className="mb-2 text-xs text-text-secondary">
        This is a single customer's full path. Left column shows the four Middleby systems
        they touched (GA4 for browsing, LogRocket for UX issues, HubSpot for the RFQ, Salesforce
        for the resulting opportunity). Each pill is one real event  -  click any pill to see the
        underlying record.
      </p>
      <div className="rounded-lg border border-border bg-surface-default" style={{ height: Math.max(280, height + 40) }}>
        <div className="relative h-full w-full">
          <LaneBackground lanes={lanes} height={height} />
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodeClick={handleNodeClick}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            proOptions={{ hideAttribution: true }}
            panOnDrag
            zoomOnScroll={false}
            zoomOnPinch
            minZoom={0.5}
            maxZoom={1.5}
          >
            <Background gap={16} size={1} color="#E5E7EB" />
            <Controls showInteractive={false} className="!bottom-2 !left-auto !right-2" />
          </ReactFlow>
        </div>
      </div>

      <EventDetailPanel event={selected} onClose={() => setSelected(null)} />
    </BlockShell>
  );
}
