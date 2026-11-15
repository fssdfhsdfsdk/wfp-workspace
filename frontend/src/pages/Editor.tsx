/* 编排：React Flow 图 ⇄ Monaco YAML 双栏，最小 patch / 300ms 防抖 / 冲突回滚 /
   rjsf 参数表单 / lint 跳转 / 撤销重做 / 布局分离。
   Implements: DR-20, DR-43, DR-44, DR-45, DR-46, DR-47, DR-48, DR-29 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, toast } from '../api';
import { CodeEditor, DiffView, monaco, setMarkers } from '../Monaco';
import { SchemaForm } from '../SchemaForm';
import { GraphCanvas, dagreLayout } from '../GraphCanvas';
import { Empty, Modal, Section } from '../ui';

const KEY_ORDER = ['id', 'name', 'uses', 'with', 'when', 'loop', 'timeout_seconds', 'retry', 'on_error', 'compensate', 'register', 'node'];

export function EditorPage({ params }: { params: URLSearchParams }) {
  const id = params.get('id') || '';
  const [text, setText] = useState('');
  const [sha, setSha] = useState('');
  const [graph, setGraph] = useState<any>({ nodes: [], edges: [], index: {} });
  const [layout, setLayout] = useState<any>({ positions: {}, collapsed: [], groups: {} });
  const [actions, setActions] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [manifest, setManifest] = useState<any>(null);
  const [stepParams, setStepParams] = useState<any>({});
  const [mode, setMode] = useState<'both' | 'graph' | 'yaml'>('both');
  const [lint, setLint] = useState<any[]>([]);
  const [history, setHistory] = useState<string[]>([]);
  const [future, setFuture] = useState<string[]>([]);
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffText, setDiffText] = useState('');
  const editorRef = useRef<any>(null);
  const debounceRef = useRef<any>(null);
  const lastSaved = useRef('');

  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const [yaml, g, l, acts]: any = await Promise.all([
        api.get(`/api/pipelines/${id}/yaml`), api.get(`/api/pipelines/${id}/graph`),
        api.get(`/api/pipelines/${id}/layout`), api.get('/api/actions'),
      ]);
      setText(yaml.text); setSha(yaml.sha256); lastSaved.current = yaml.text;
      setGraph(g); setLayout(l || { positions: {}, collapsed: [], groups: {} }); setActions(acts);
    } catch (e: any) { toast(e.message, 'error'); }
  }, [id]);

  useEffect(() => { refresh(); }, [refresh]);

  const pushHistory = (old: string) => {
    setHistory((h) => [...h.slice(-49), old]);
    setFuture([]);
  };

  const applyPatch = async (deltas: any[]) => {
    try {
      const r: any = await api.post(`/api/pipelines/${id}/patch`, { sha256: sha, deltas });
      pushHistory(text);
      setText(r.text); setSha(r.sha256); lastSaved.current = r.text;
      const g: any = await api.get(`/api/pipelines/${id}/graph`);
      setGraph(g);
      toast('最小 patch 已应用，注释/键序保留（DR-45）');
    } catch (e: any) {
      toast(`${e.message}；以真源为准重新载入`, 'warn'); await refresh();
    }
  };

  const saveYaml = async (next: string) => {
    if (next === lastSaved.current) return;
    try {
      const r: any = await api.put(`/api/pipelines/${id}/yaml`, { yaml: next }, { 'If-Match': sha });
      pushHistory(text);
      setSha(r.sha256); lastSaved.current = next;
      const g: any = await api.get(`/api/pipelines/${id}/graph`);
      setGraph(g);
      toast('YAML 真源已保存，图已重新投影');
    } catch (e: any) {
      toast(`${e.message}；冲突时以真源为准（DR-45）`, 'warn'); await refresh();
    }
  };

  const onYamlChange = (v: string) => {
    setText(v);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => saveYaml(v), 300);
  };

  const undo = async () => {
    if (!history.length) return;
    const prev = history[history.length - 1];
    setFuture((f) => [text, ...f]);
    setHistory((h) => h.slice(0, -1));
    setText(prev); await saveYaml(prev);
  };
  const redo = async () => {
    if (!future.length) return;
    const next = future[0];
    setFuture((f) => f.slice(1));
    setHistory((h) => [...h, text]);
    setText(next); await saveYaml(next);
  };

  const selectStep = async (sid: string) => {
    setSelectedId(sid);
    const node = (graph.nodes || []).find((n: any) => n.id === sid);
    const uses = node?.uses || '';
    const [aid, ver] = uses.split('@');
    try {
      const v: any = await api.get(`/api/actions/${aid}/versions/${ver || '1.0.0'}`);
      setManifest(v.manifest);
      const found = (graph.nodes || []).find((n: any) => n.id === sid);
      setStepParams(found?.with || {});
    } catch { setManifest(null); }
  };

  const onParamChange = (data: any) => {
    setStepParams(data);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      if (!selectedId) return;
      // 逐字段生成最小 patch，只触及 steps[i].with（DR-45/48）
      const deltas = Object.keys(data || {}).map((k) => ({
        kind: 'update_param', step_id: selectedId, field: k, value: data[k],
      }));
      if (deltas.length) applyPatch(deltas);
    }, 300);
  };

  const runLint = async () => {
    try {
      const r: any = await api.post(`/api/pipelines/${id}/lint`);
      setLint(r.issues || []);
      if (editorRef.current) {
        setMarkers(editorRef.current, (r.issues || []).map((i: any) => ({
          line: i.line || 1, message: `${i.code} ${i.message}`, severity: i.level,
        })));
      }
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const jumpTo = (line: number) => {
    if (editorRef.current && line > 0) editorRef.current.revealLineInCenter(line);
  };

  const onLayoutChange = async (positions: any) => {
    const next = { ...layout, positions };
    setLayout(next);
    try { await api.put(`/api/pipelines/${id}/layout`, next); toast('布局已写入 pipeline.layout.json（语义无关，DR-46）'); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  const insertStep = (ref: string, position?: any) => {
    const step: any = { id: 's' + Math.random().toString(36).slice(2, 7), uses: ref, with: {} };
    KEY_ORDER.forEach((k) => { if (!(k in step)) delete step[k]; });
    applyPatch([{ kind: 'insert_step', new_step: step }]);
    if (position) {
      const next = { ...layout, positions: { ...(layout.positions || {}), [step.id]: position } };
      setLayout(next); api.put(`/api/pipelines/${id}/layout`, next).catch(() => {});
    }
  };

  const autoLayout = () => {
    const nodes = (graph.nodes || []).map((n: any, i: number) => ({ id: n.id, position: { x: 0, y: i * 104 } }));
    const edges = nodes.slice(1).map((n: any, i: number) => ({ source: nodes[i].id, target: n.id }));
    onLayoutChange(dagreLayout(nodes, edges, 'TB'));
  };

  if (!id) return <div className="page"><Empty text="请从工作项列表选择一个工作项进入编排页。" /></div>;

  return <div className="page editor-page">
    <div className="page-head"><div><h1>编排 · {id}</h1>
      <p className="sub">YAML 唯一真源；图编辑只产生 steps[i] 最小 patch；布局独立文件（DR-45/46）</p></div>
      <div className="row">
        {(['both', 'graph', 'yaml'] as const).map((m) => <button key={m}
          className={`btn tiny ${mode === m ? 'primary' : ''}`} onClick={() => setMode(m)}>
          {{ both: '双栏', graph: '仅图', yaml: '仅 YAML' }[m]}</button>)}
        <button className="btn tiny" onClick={undo} disabled={!history.length}>撤销 ({history.length})</button>
        <button className="btn tiny" onClick={redo} disabled={!future.length}>重做</button>
        <button id="wfp-save-btn" className="btn tiny" onClick={() => saveYaml(text)}>保存 YAML</button>
        <button className="btn tiny" onClick={runLint}>lint</button>
        <button className="btn tiny" onClick={autoLayout}>dagre</button>
        <button className="btn tiny" onClick={async () => { const d: any = await api.get(`/api/pipelines/${id}/yaml`); setDiffText(d.text); setDiffOpen(true); }}>与上一版 diff</button>
      </div></div>

    <div className={`grid cols3 ${mode === 'both' ? '' : 'single'}`}>
      {mode !== 'yaml' && <Section title="动作面板（拖拽到画布）" className="palette">
        {actions.map((a) => <div key={a.id} className="palette-item" draggable
          onDragStart={(e) => e.dataTransfer.setData('application/wfp-action', `${a.id}@${a.latest_version}`)}
          onDoubleClick={() => insertStep(`${a.id}@${a.latest_version}`)}>
          <b className="mono">{a.id}</b><div className="tiny muted">{a.name} · {a.runtime}</div>
        </div>)}
        <div className="divider" />
        <h3>步骤参数（rjsf，manifest 驱动）</h3>
        {!selectedId ? <Empty text="点击画布节点选择步骤" /> : <>
          <p className="tiny mono">{selectedId} · {manifest?.id || ''}</p>
          <SchemaForm schema={manifest?.params || { type: 'object', properties: {} }}
            formData={stepParams} onChange={onParamChange} />
          <div className="row">
            <button className="btn tiny" onClick={() => applyPatch([{ kind: 'wrap_container', step_id: selectedId, container: 'when', expr: "inputs.flag == true" }])}>加 when 分支</button>
            <button className="btn tiny" onClick={() => applyPatch([{ kind: 'wrap_container', step_id: selectedId, container: 'loop', expr: '[1,2,3]' }])}>加 loop 容器</button>
          </div>
        </>}
      </Section>}

      {mode !== 'yaml' && <Section title="图视图（React Flow 投影）" className="graph-section">
        <GraphCanvas graph={graph} layout={layout} selectedId={selectedId}
          onInsert={insertStep} onReorder={(from: number, to: number) => applyPatch([{ kind: 'move_step', src: from, dst: to }])}
          onDelete={(ids: string[]) => applyPatch(ids.map((sid) => ({ kind: 'remove_step', step_id: sid })))}
          onSelect={selectStep} onLayoutChange={onLayoutChange} />
      </Section>}

      {mode !== 'graph' && <Section title={`YAML 真源（Monaco） · sha ${sha.slice(0, 10)}`} className="yaml-section">
        <CodeEditor value={text} height={460} onChange={onYamlChange}
          onMount={(editor) => { editorRef.current = editor; if (lint.length) runLint(); }} />
        <div className="lint-panel">
          <h3>问题面板（lint，点击跳转到 YAML 行列）</h3>
          {!lint.length ? <div className="tiny muted">点击 lint 执行静态校验</div> :
            lint.map((i: any, k: number) => <div key={k} className={`issue ${i.level}`} onClick={() => jumpTo(i.line)}>
              {i.code} · 第 {i.line} 行 · {i.path}：{i.message}</div>)}
        </div>
      </Section>}
    </div>

    {diffOpen && <Modal title="YAML 与上一版 diff（Monaco）" wide onClose={() => setDiffOpen(false)}>
      <DiffView before={diffText} after={text} height={420} />
    </Modal>}
  </div>;
}
