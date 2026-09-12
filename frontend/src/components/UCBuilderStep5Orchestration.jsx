import { useMemo, useState, useCallback, useEffect, useRef } from "react";
import {
  ReactFlow, Background, Controls, useNodesState, useEdgesState,
  Handle, Position, addEdge, BaseEdge, getSmoothStepPath, EdgeLabelRenderer,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  GitBranch, BarChart3, Calendar, Layers, LayoutGrid, Workflow,
  RefreshCw, Info, Plus, Trash2, X, Check,
} from "lucide-react";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { deriveTasksFromPlanItems, resolveContextualPlanItems } from "../storage/use-case-draft";

// ── Agent type definitions ────────────────────────────────────────────────────
const AGENT_DEFS = {
  retriever: { label: "Retriever Agent", icon: Layers,    bg: "bg-surface-secondary", text: "text-text-tertiary", border: "border-border", nodeBorder: "#a4a7ae" },
  analytics: { label: "Analytics Agent", icon: BarChart3, bg: "bg-primary-light", text: "text-primary", border: "border-primary/30", nodeBorder: "#6941c6" },
  planning:  { label: "Planning Agent",  icon: Calendar,  bg: "bg-[#ecfdf3]", text: "text-[#067647]", border: "border-[#a7f3d0]", nodeBorder: "#067647" },
  display:   { label: "Display Agent",   icon: GitBranch, bg: "bg-primary-light dark:bg-primary/20", text: "text-primary", border: "border-primary/35", nodeBorder: "#6941c6" },
  action:    { label: "Action Agent",    icon: Layers,    bg: "bg-[#fff6ed]", text: "text-[#b93815]", border: "border-[#fdba74]", nodeBorder: "#b93815" },
  guard:     { label: "Guard Agent",     icon: GitBranch, bg: "bg-[#fef3f2]", text: "text-[#b42318]", border: "border-[#fca5a5]", nodeBorder: "#b42318" },
};

const OUTPUT_OPTIONS = [
  { value: "action",        label: "Action" },
  { value: "display-data",  label: "Display Data" },
  { value: "display-kpi",   label: "Display KPI" },
  { value: "alert",         label: "Alert" },
  { value: "decision-card", label: "Decision Card" },
  { value: "auto-execute",  label: "Auto-execute" },
];

const OUTPUT_LABEL = Object.fromEntries(OUTPUT_OPTIONS.map((o) => [o.value, o.label]));
const AGENT_KEYS = Object.keys(AGENT_DEFS);

// ── Custom deletable edge ─────────────────────────────────────────────────────
function DeletableEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, style, markerEnd, data }) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const isHovered = data?.isHovered ?? false;

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <div
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)` }}
          className="nodrag nopan pointer-events-none absolute"
        >
          {isHovered && (
            <button
              type="button"
              onClick={() => data?.onDelete(id)}
              title="Remove connection"
              className="pointer-events-auto flex h-6 w-6 items-center justify-center rounded-full border border-[#fca5a5] bg-surface text-sm font-bold text-[#b42318] shadow-md transition hover:bg-[#fef3f2]"
            >
              ✕
            </button>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

// ── Custom React Flow Node ────────────────────────────────────────────────────
function AgentNode({ data }) {
  const def = AGENT_DEFS[data.agentType] ?? AGENT_DEFS.analytics;
  const Icon = def.icon;

  return (
    <div
      className="rounded-xl border-2 bg-surface shadow-md transition-all hover:shadow-lg"
      style={{ borderColor: def.nodeBorder, minWidth: 185, maxWidth: 225 }}
    >
      <Handle type="target" position={Position.Top} className="!border-border-light !bg-surface" />

      <div className={`flex items-center gap-2 rounded-t-[10px] px-3 py-2 ${def.bg}`}>
        <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface ${def.text}`}>
          <Icon size={14} strokeWidth={2} />
        </div>
        <p className={`text-xs font-semibold ${def.text} truncate`}>{data.label}</p>
      </div>

      <div className="px-3 py-2 space-y-1.5">
        <div className="flex items-center justify-between gap-1">
          <span className={`rounded-full ${def.bg} ${def.text} px-2 py-0.5 text-[10px] font-semibold`}>
            {def.label}
          </span>
          <span className="text-[10px] text-text-muted">{OUTPUT_LABEL[data.outputType] ?? "Action"}</span>
        </div>
        <div className="flex items-center justify-center gap-1 rounded-md border border-dashed border-border-light py-1 text-[10px] text-text-muted">
          <RefreshCw size={9} />
          Click to cycle agent
        </div>
      </div>

      <Handle type="source" position={Position.Bottom} className="!border-border-light !bg-surface" />
    </div>
  );
}

const NODE_TYPES = { agentNode: AgentNode };
const EDGE_TYPES = { deletable: DeletableEdge };

// ── Add task modal ────────────────────────────────────────────────────────────
function AddTaskModal({ onAdd, onClose }) {
  const [name, setName] = useState("");
  const [agentType, setAgentType] = useState("analytics");
  const [outputType, setOutputType] = useState("action");
  const inputRef = useRef(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const handleAdd = () => {
    if (!name.trim()) return;
    onAdd({ name: name.trim(), agentType, outputType });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-surface shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#f2f2f5] px-5 py-4">
          <h3 className="text-sm font-semibold text-text-primary">Add task node</h3>
          <button type="button" onClick={onClose} className="text-text-muted hover:text-text-primary">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-5 px-5 py-5">
          {/* Name */}
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-text-secondary">Task name</label>
            <input
              ref={inputRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
              placeholder="e.g. Validate supplier availability"
              className="w-full rounded-lg border border-border-light px-3 py-2 text-sm text-text-primary placeholder-[#717680] outline-none focus:border-primary focus:ring-1 focus:ring-[#6941c6]"
            />
          </div>

          {/* Agent type */}
          <div>
            <label className="mb-2 block text-xs font-semibold text-text-secondary">Agent type</label>
            <div className="flex flex-wrap gap-1.5">
              {AGENT_KEYS.map((key) => {
                const def = AGENT_DEFS[key];
                const sel = agentType === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setAgentType(key)}
                    className={`rounded-full px-2.5 py-1 text-xs font-semibold transition ${
                      sel ? `${def.bg} ${def.text} ring-2 ring-current ring-offset-1` : "bg-surface-tertiary text-text-secondary hover:bg-border"
                    }`}
                  >
                    {def.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Output type */}
          <div>
            <label className="mb-2 block text-xs font-semibold text-text-secondary">Output type</label>
            <div className="flex flex-wrap gap-1.5">
              {OUTPUT_OPTIONS.map((o) => {
                const sel = outputType === o.value;
                return (
                  <button
                    key={o.value}
                    type="button"
                    onClick={() => setOutputType(o.value)}
                    className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition ${
                      sel ? "border-primary bg-primary-light dark:bg-primary/20 text-primary" : "border-border bg-surface text-text-secondary hover:border-primary"
                    }`}
                  >
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-[#f2f2f5] px-5 py-4">
          <button type="button" onClick={onClose} className="rounded-lg border border-border-light px-4 py-2 text-sm font-medium text-text-secondary hover:bg-surface-secondary">
            Cancel
          </button>
          <button
            type="button"
            onClick={handleAdd}
            disabled={!name.trim()}
            className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:opacity-40"
          >
            <Check size={14} />
            Add to flow
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Layout helper ─────────────────────────────────────────────────────────────
function makeEdge(source, target, onDelete) {
  return {
    id: `e-${source}-${target}`,
    source,
    target,
    animated: true,
    type: "deletable",
    style: { stroke: "#6941c6", strokeWidth: 2 },
    data: { onDelete },
  };
}

function tasksToFlow(tasks, onDelete) {
  const COLS = 3;
  const COL_WIDTH = 250;
  const ROW_HEIGHT = 190;
  const H_GAP = 20;
  const V_GAP = 40;

  const nodes = tasks.map((task, i) => {
    const row = Math.floor(i / COLS);
    const col = i % COLS;
    const x = row % 2 === 0 ? col * (COL_WIDTH + H_GAP) : (COLS - 1 - col) * (COL_WIDTH + H_GAP);
    const y = row * (ROW_HEIGHT + V_GAP);
    return {
      id: task.id,
      type: "agentNode",
      position: { x, y },
      data: { label: task.name, agentType: task.agentType, outputType: task.outputType },
    };
  });

  const edges = tasks.slice(0, -1).map((task, i) =>
    makeEdge(task.id, tasks[i + 1].id, onDelete)
  );

  return { nodes, edges };
}

// ── Cards view ────────────────────────────────────────────────────────────────
function CardsView({ tasks, onDelete }) {
  return (
    <div className="space-y-3">
      {tasks.map((task, i) => {
        const def = AGENT_DEFS[task.agentType] ?? AGENT_DEFS.analytics;
        const Icon = def.icon;
        return (
          <div key={task.id} className="group flex items-center gap-4 rounded-xl border border-border bg-surface px-4 py-3 shadow-sm">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary-light dark:bg-primary/20 text-xs font-bold text-primary">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-text-primary">{task.name}</p>
              <p className="mt-0.5 text-xs text-text-muted">{OUTPUT_LABEL[task.outputType] ?? "Action"}</p>
            </div>
            <div className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${def.bg} ${def.text}`}>
              <Icon size={12} strokeWidth={2} />
              {def.label}
            </div>
            <button
              type="button"
              onClick={() => onDelete(task.id)}
              className="ml-1 shrink-0 text-[#d5d7da] opacity-0 transition hover:text-[#b42318] group-hover:opacity-100"
            >
              <Trash2 size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function UCBuilderStep5Orchestration() {
  const { draft, updateDraft } = useUseCaseDraft();
  const { entries } = useUseCaseCatalog();
  const viewMode = draft.step6?.viewMode ?? "flow";
  const [showAddModal, setShowAddModal] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const [hoveredEdgeId, setHoveredEdgeId] = useState(null);

  const autonomyLevel = draft.step1?.autonomyLevel || "copilot";

  // Resolve the catalog entry this draft belongs to so orchestration tasks
  // reflect the specific use case's own plan (not a generic category set).
  const catalogEntry = useMemo(() => {
    const targetId =
      draft.builderSession?.reopenedCatalogId ||
      draft.builderSession?.fromSeedTemplateId ||
      null;
    if (targetId) {
      const found = (entries || []).find((e) => e.id === targetId);
      if (found) return found;
    }
    return draft.step1?.title ? { title: draft.step1.title } : null;
  }, [entries, draft.builderSession, draft.step1?.title]);

  // Contextual tasks derived from Step 4 plan items when Step 5 tasks are
  // absent - keeps orchestration in sync with the use case's actual plan
  // instead of showing a generic default set.
  const contextualTasks = useMemo(() => {
    const items = draft.step4?.planItems && draft.step4.planItems.length > 0
      ? draft.step4.planItems
      : resolveContextualPlanItems(
          draft.step2?.primaryQuestion ?? "",
          draft.step1?.category ?? "",
          (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled),
          (draft.step3?.confirmedKnowledge ?? []).filter((k) => k.enabled),
          draft.step1?.title ?? "",
          catalogEntry
        );
    return deriveTasksFromPlanItems(items, autonomyLevel);
  }, [draft, autonomyLevel, catalogEntry]);

  const tasks = (draft.step5?.tasks?.length ? draft.step5.tasks : contextualTasks).map((t) => ({
    ...t,
    agentType: t.agentType || "analytics",
    outputType: t.outputType || "action",
  }));

  const setViewMode = (mode) =>
    updateDraft((d) => ({ ...d, step6: { ...d.step6, viewMode: mode } }));

  // Cycle agent type on node click
  const handleNodeClick = useCallback((_, node) => {
    const taskId = node.id;
    const currentType = node.data.agentType;
    const idx = AGENT_KEYS.indexOf(currentType);
    const nextType = AGENT_KEYS[(idx + 1) % AGENT_KEYS.length];
    updateDraft((d) => ({
      ...d,
      step5: {
        ...d.step5,
        tasks: (d.step5?.tasks?.length ? d.step5.tasks : contextualTasks).map((t) =>
          t.id === taskId ? { ...t, agentType: nextType } : t
        ),
      },
    }));
  }, [updateDraft]);

  // Add a new task
  const handleAddTask = useCallback(({ name, agentType, outputType }) => {
    const id = `t${Date.now()}`;
    const newTask = { id, name, agentType, outputType, estimatedTime: "~1 min", chartType: "table", kpiIds: [] };
    updateDraft((d) => ({
      ...d,
      step5: { ...d.step5, tasks: [...(d.step5?.tasks?.length ? d.step5.tasks : contextualTasks), newTask] },
    }));
    setShowAddModal(false);
  }, [updateDraft]);

  // Delete a task
  const handleDeleteTask = useCallback((taskId) => {
    updateDraft((d) => ({
      ...d,
      step5: {
        ...d.step5,
        tasks: (d.step5?.tasks?.length ? d.step5.tasks : contextualTasks).filter((t) => t.id !== taskId),
      },
    }));
  }, [updateDraft]);

  // Delete selected nodes via keyboard
  const handleKeyDown = useCallback((e) => {
    if ((e.key === "Delete" || e.key === "Backspace") && selectedIds.length > 0) {
      updateDraft((d) => ({
        ...d,
        step5: {
          ...d.step5,
          tasks: (d.step5?.tasks?.length ? d.step5.tasks : contextualTasks).filter((t) => !selectedIds.includes(t.id)),
        },
      }));
      setSelectedIds([]);
    }
  }, [selectedIds, updateDraft]);

  // Initialise without onDelete - injected via effect once setEdges is available
  const { nodes: initNodes, edges: initEdges } = tasksToFlow(tasks, null);
  const [nodes, setNodes, onNodesChange] = useNodesState(initNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initEdges);

  const handleDeleteEdge = useCallback((edgeId) => {
    setEdges((eds) => eds.filter((e) => e.id !== edgeId));
  }, [setEdges]);

  // Inject onDelete into edges after handleDeleteEdge is available
  useEffect(() => {
    setEdges((eds) => eds.map((e) => ({ ...e, data: { ...e.data, onDelete: handleDeleteEdge } })));
  }, [handleDeleteEdge, setEdges]);

  // Sync isHovered flag on edges
  useEffect(() => {
    setEdges((eds) => eds.map((e) => ({ ...e, data: { ...e.data, isHovered: e.id === hoveredEdgeId } })));
  }, [hoveredEdgeId, setEdges]);

  // Sync node data when tasks update
  useEffect(() => {
    setNodes((nds) =>
      nds.map((n) => {
        const task = tasks.find((t) => t.id === n.id);
        if (!task) return n;
        return { ...n, data: { ...n.data, agentType: task.agentType, outputType: task.outputType, label: task.name } };
      })
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.step5?.tasks]);

  // Add new task node to canvas when tasks array grows
  useEffect(() => {
    const { nodes: freshNodes, edges: freshEdges } = tasksToFlow(tasks, handleDeleteEdge);
    const existingIds = new Set(nodes.map((n) => n.id));
    const newNodes = freshNodes.filter((n) => !existingIds.has(n.id));
    if (newNodes.length > 0) {
      setNodes((nds) => [...nds, ...newNodes]);
      setEdges(freshEdges);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks.length]);

  const onConnect = useCallback(
    (params) => setEdges((eds) => addEdge(
      { ...params, animated: true, type: "deletable", style: { stroke: "#6941c6", strokeWidth: 2 }, data: { onDelete: handleDeleteEdge } },
      eds
    )),
    [setEdges, handleDeleteEdge]
  );

  const onSelectionChange = useCallback(({ nodes: sel }) => {
    setSelectedIds(sel.map((n) => n.id));
  }, []);

  // Tasks ordered by current node positions so Cards view mirrors the Flow layout
  const flowTasks = nodes
    .slice()
    .sort((a, b) => a.position.y - b.position.y || a.position.x - b.position.x)
    .map((n) => tasks.find((t) => t.id === n.id))
    .filter(Boolean);

  return (
    <>
      {showAddModal && <AddTaskModal onAdd={handleAddTask} onClose={() => setShowAddModal(false)} />}

      <UCBuilderLayout
        currentStep={6}
        title="Orchestration Agent"
        subtitle="Edit the flow: drag nodes to reposition, connect handles to link tasks, click a node to cycle its agent type."
        backPath="/use-cases/builder/step-5"
        nextPath="/use-cases/builder/step-7"
        wide
      >
        <div className="space-y-4">
          {/* Toolbar */}
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2 rounded-lg border border-primary/35 bg-primary-light dark:bg-primary/15 px-3 py-2">
              <Info size={14} className="shrink-0 text-primary" />
              <p className="text-xs text-text-secondary">
                Drag nodes to reposition. Drag from a node handle to connect. Click a node to cycle its agent. Select + Delete key to remove.
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={() => setShowAddModal(true)}
                className="flex items-center gap-1.5 rounded-lg border border-primary bg-surface px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary-light dark:bg-primary/15"
              >
                <Plus size={13} />
                Add task
              </button>
              <div className="flex items-center gap-1 rounded-lg border border-border p-0.5">
                <button
                  type="button"
                  onClick={() => setViewMode("flow")}
                  className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${viewMode === "flow" ? "bg-[#181d27] text-white" : "text-text-secondary hover:text-text-primary"}`}
                >
                  <Workflow size={13} />
                  Flow
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("cards")}
                  className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${viewMode === "cards" ? "bg-[#181d27] text-white" : "text-text-secondary hover:text-text-primary"}`}
                >
                  <LayoutGrid size={13} />
                  Cards
                </button>
              </div>
            </div>
          </div>

          {viewMode === "flow" ? (
            <div
              className="h-[560px] w-full overflow-hidden rounded-2xl border border-border bg-surface-secondary"
              onKeyDown={handleKeyDown}
              tabIndex={0}
            >
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onNodeClick={handleNodeClick}
                onSelectionChange={onSelectionChange}
                onEdgeMouseEnter={(_, edge) => setHoveredEdgeId(edge.id)}
                onEdgeMouseLeave={() => setHoveredEdgeId(null)}
                nodeTypes={NODE_TYPES}
                edgeTypes={EDGE_TYPES}
                fitView
                fitViewOptions={{ padding: 0.3 }}
                nodesDraggable
                nodesConnectable
                elementsSelectable
                deleteKeyCode={null}
                proOptions={{ hideAttribution: true }}
              >
                <Background color="#e4e4e7" gap={20} size={1} />
                <Controls className="[&>button]:border-border [&>button]:bg-surface" />
              </ReactFlow>
            </div>
          ) : (
            <CardsView tasks={flowTasks} onDelete={handleDeleteTask} />
          )}

          {/* Agent legend */}
          <div className="flex flex-wrap gap-2">
            {Object.entries(AGENT_DEFS).map(([key, def]) => {
              const Icon = def.icon;
              return (
                <span key={key} className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${def.bg} ${def.text} ${def.border}`}>
                  <Icon size={11} strokeWidth={2} />
                  {def.label}
                </span>
              );
            })}
          </div>
        </div>
      </UCBuilderLayout>
    </>
  );
}
