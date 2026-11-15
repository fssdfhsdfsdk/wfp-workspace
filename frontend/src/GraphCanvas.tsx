/* React Flow 图视图：拖拽插入、连线表达顺序、容器节点、dagre 自动布局、多选删除。
   Implements: DR-20, DR-21, DR-43, DR-44, DR-45, DR-46 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position, MarkerType,
  ReactFlowProvider, useNodesState, useEdgesState, useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';

const NODE_W = 250;
const NODE_H = 76;

function StepNode({ data, selected }: any) {
  return <div className={`rf-step ${selected ? 'selected' : ''} ${data.status || ''}`}>
    <Handle type="target" position={Position.Top} />
    <div className="rf-title">{data.label || data.id}</div>
    <div className="rf-uses">{data.uses}</div>
    <div className="rf-badges">
      {data.when ? <span className="badge warn">when</span> : null}
      {data.loop ? <span className="badge warn">loop</span> : null}
      {data.compensate === 'none' ? <span className="badge err">不可补偿</span> : null}
      {data.unpredictable ? <span className="badge err">unpredictable</span> : null}
    </div>
    <Handle type="source" position={Position.Bottom} />
  </div>;
}

function ContainerNode({ data }: any) {
  return <div className="rf-container">
    <Handle type="target" position={Position.Top} />
    <b>{data.kind === 'loop' ? '循环容器' : '分支容器'}</b>
    <div className="tiny">{data.expr || ''}</div>
    <Handle type="source" position={Position.Bottom} />
  </div>;
}

const nodeTypes = { step: StepNode, container: ContainerNode };

export function dagreLayout(nodes: any[], edges: any[], direction = 'TB'): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 36, ranksep: 64 });
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  const out: Record<string, { x: number; y: number }> = {};
  nodes.forEach((n) => {
    const p = g.node(n.id);
    out[n.id] = { x: Math.round(p.x - NODE_W / 2), y: Math.round(p.y - NODE_H / 2) };
  });
  return out;
}

function Inner(props: any) {
  const { graph, layout, selectedId, onInsert, onReorder, onDelete, onSelect, onLayoutChange } = props;
  const { screenToFlowPosition, fitView } = useReactFlow();
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([]);
  const [selection, setSelection] = useState<string[]>([]);

  const computed = useMemo(() => {
    const positions = layout?.positions || {};
    const nodes = (graph.nodes || []).map((n: any, i: number) => ({
      id: n.id, type: 'step', position: positions[n.id] || { x: 40, y: 20 + i * 104 },
      data: { ...n, selected: n.id === selectedId },
      selected: n.id === selectedId,
    }));
    const edges = nodes.slice(1).map((n: any, i: number) => ({
      id: `e-${i}`, source: nodes[i].id, target: n.id, type: 'smoothstep',
      markerEnd: { type: MarkerType.ArrowClosed }, animated: false,
    }));
    return { nodes, edges };
  }, [graph, layout, selectedId]);

  useEffect(() => {
    setRfNodes(computed.nodes);
    setRfEdges(computed.edges);
  }, [computed, setRfNodes, setRfEdges]);

  const onDrop = useCallback((event: any) => {
    event.preventDefault();
    const ref = event.dataTransfer.getData('application/wfp-action');
    if (!ref) return;
    const pos = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    onInsert(ref, pos);
  }, [onInsert, screenToFlowPosition]);

  const onDragStop = useCallback((_: any, node: any) => {
    const ids: string[] = computed.nodes.map((n: any) => n.id);
    const sorted = [...rfNodes].sort((a: any, b: any) => a.position.y - b.position.y);
    const from = ids.indexOf(node.id);
    const to = sorted.findIndex((n: any) => n.id === node.id);
    if (from !== to && to >= 0) onReorder(from, to);
  }, [computed.nodes, rfNodes, onReorder]);

  const onConnect = useCallback((conn: any) => {
    const ids: string[] = computed.nodes.map((n: any) => n.id);
    const from = ids.indexOf(conn.source);
    const to = ids.indexOf(conn.target);
    if (from >= 0 && to >= 0 && from !== to) onReorder(from, to);
  }, [computed.nodes, onReorder]);

  return <div className="graph-canvas" onDrop={onDrop} onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; }}>
    <div className="graph-toolbar">
      <button className="btn tiny" onClick={() => onLayoutChange(dagreLayout(computed.nodes, computed.edges, 'TB'))}>dagre 自动布局</button>
      <button className="btn tiny" onClick={() => onLayoutChange(dagreLayout(computed.nodes, computed.edges, 'LR'))}>横向布局</button>
      <button className="btn tiny danger" disabled={!selection.length}
        onClick={() => { onDelete(selection); setSelection([]); }}>删除选中（{selection.length}）</button>
      <button className="btn tiny" onClick={() => fitView({ padding: 0.2 })}>适应画布</button>
    </div>
    <ReactFlow
      nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => onSelect(node.id)}
      onNodeDragStop={onDragStop} onConnect={onConnect}
      onNodesDelete={(deleted) => onDelete(deleted.map((n: any) => n.id))}
      onSelectionChange={({ nodes }: any) => setSelection(nodes.map((n: any) => n.id))}
      fitView minZoom={0.3} maxZoom={2} deleteKeyCode={['Backspace', 'Delete']}
      multiSelectionKeyCode={['Meta', 'Control']}
    >
      <Background color="#2b3650" gap={18} />
      <Controls />
      <MiniMap pannable zoomable nodeColor="#5b8cff" />
    </ReactFlow>
  </div>;
}

export function GraphCanvas(props: any) {
  return <ReactFlowProvider><Inner {...props} /></ReactFlowProvider>;
}
