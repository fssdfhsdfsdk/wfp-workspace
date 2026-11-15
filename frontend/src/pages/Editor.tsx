/* 编排：React Flow 图 ⇄ Monaco YAML 双栏，最小 patch / 300ms 防抖 / 冲突回滚 /
   rjsf 参数表单 / lint 跳转 / 撤销重做 / 布局分离 / 拖拽新建工作项 / 点击节点编辑参数。
   Implements: DR-14, DR-20, DR-27, DR-28, DR-43, DR-44, DR-45, DR-46, DR-47, DR-48, DR-29 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, toast } from '../api';
import { CodeEditor, DiffView, monaco, setMarkers } from '../Monaco';
import { SchemaForm } from '../SchemaForm';
import { StepParamsModal } from '../StepParamsModal';
import { GraphCanvas, dagreLayout } from '../GraphCanvas';
import { Empty, Modal, Section } from '../ui';

const KEY_ORDER = ['id', 'name', 'uses', 'with', 'when', 'loop', 'timeout_seconds', 'retry', 'on_error', 'compensate', 'register', 'node'];

function previewYaml(idText: string, name: string, steps: any[]): string {
  const head = `apiVersion: wfp/v1\nmeta:\n  id: ${idText || '<未填写>'}\n  name: ${name || idText || '<未填写>'}\n  version: 1.0.0\nsteps:\n`;
  return head + (steps.length ? steps.map((s) =>
    `  - id: ${s.id}\n    uses: ${s.uses}\n    with: ${JSON.stringify(s.with || {})}`).join('\n')
    : '  # 从左侧动作面板拖入动作');
}

export function EditorPage({ params }: { params: URLSearchParams }) {
  const id = params.get('id') || '';
  const isDraft = !id;
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
  const [draftId, setDraftId] = useState('');
  const [draftName, setDraftName] = useState('');
  const [draftWorkdir, setDraftWorkdir] = useState('');
  const [draftAllowOutside, setDraftAllowOutside] = useState(false);
  const [metaOpen, setMetaOpen] = useState(false);
  const [metaForm, setMetaForm] = useState<any>({ workdir: '', allow_outside_workspace: false });
  const [draftSteps, setDraftSteps] = useState<any[]>([]);
  const [draftLayout, setDraftLayout] = useState<any>({ positions: {}, collapsed: [], groups: {} });
  const [saving, setSaving] = useState(false);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [paramStep, setParamStep] = useState<any>(null);
  const editorRef = useRef<any>(null);
  const debounceRef = useRef<any>(null);
  const lastSaved = useRef('');

  const draftGraph = useMemo(() => {
    const nodes = draftSteps.map((s, i) => ({ id: s.id, index: i, label: s.name || s.id,
      uses: s.uses, with: s.with || {}, when: s.when, loop: s.loop }));
    return { nodes, edges: nodes.slice(1).map((n, i) => ({ source: nodes[i].id, target: n.id })),
      index: Object.fromEntries(nodes.map((n, i) => [n.id, `steps[${i}]`])) };
  }, [draftSteps]);
  const activeGraph = isDraft ? draftGraph : graph;
  const activeLayout = isDraft ? draftLayout : layout;

  const refresh = useCallback(async () => {
    if (!id) {
      try { setActions(await api.get('/api/actions')); } catch (e: any) { toast(e.message, 'error'); }
      return;
    }
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
      setGraph(await api.get(`/api/pipelines/${id}/graph`));
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
      setGraph(await api.get(`/api/pipelines/${id}/graph`));
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
    const node = (activeGraph.nodes || []).find((n: any) => n.id === sid);
    if (!node) return;
    setParamStep(node);
    setStepParams(node.with || {});
    const [aid, ver] = String(node.uses || '').split('@');
    try {
      const v: any = await api.get(`/api/actions/${aid}/versions/${ver || '1.0.0'}`);
      setManifest(v.manifest);
    } catch { setManifest(null); }
    setParamsOpen(true);   // 点击画布动作节点即可编辑参数
  };

  const saveParams = async (next: any) => {
    setParamsOpen(false);
    if (!paramStep) return;
    setStepParams(next);
    if (isDraft) {
      setDraftSteps((s) => s.map((x) => (x.id === paramStep.id ? { ...x, with: next } : x)));
      toast('草稿参数已更新，点击「保存工作项」写入 YAML 真源');
    } else {
      await applyPatch([{ kind: 'set_with', step_id: paramStep.id, value: next }]);
    }
  };

  const onParamChange = (data: any) => {
    setStepParams({ ...stepParams, ...data });
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      if (!selectedId) return;
      const deltas = Object.keys(data || {}).map((k) => ({
        kind: 'update_param', step_id: selectedId, field: k, value: data[k],
      }));
      if (!deltas.length) return;
      if (isDraft) {
        setDraftSteps((s) => s.map((x) => (x.id === selectedId ? { ...x, with: { ...(x.with || {}), ...data } } : x)));
      } else {
        applyPatch(deltas);
      }
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
    const next = { ...activeLayout, positions };
    if (isDraft) { setDraftLayout(next); return; }
    setLayout(next);
    try { await api.put(`/api/pipelines/${id}/layout`, next); toast('布局已写入 pipeline.layout.json（语义无关，DR-46）'); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  const dropIndex = (position?: any): number => {
    if (!position) return activeGraph.nodes.length;
    const positions = activeLayout.positions || {};
    let idx = activeGraph.nodes.length;
    activeGraph.nodes.forEach((n: any, i: number) => {
      const y = positions[n.id]?.y ?? 20 + i * 104;
      if (y > position.y && i < idx) idx = i;
    });
    return idx;
  };

  const insertStep = (ref: string, position?: any) => {
    const step: any = { id: 's' + Math.random().toString(36).slice(2, 7), uses: ref, with: {} };
    KEY_ORDER.forEach((k) => { if (!(k in step)) delete step[k]; });
    const idx = dropIndex(position);
    if (isDraft) {
      setDraftSteps((s) => { const next = [...s]; next.splice(idx, 0, step); return next; });
      if (position) setDraftLayout((l: any) => ({ ...l, positions: { ...(l.positions || {}), [step.id]: position } }));
      toast(`已拖入 ${ref}（第 ${idx + 1} 步），点击「保存工作项」写入 YAML 真源`);
    } else {
      applyPatch([{ kind: 'insert_step', new_step: step, index: idx }]);
      if (position) {
        const next = { ...layout, positions: { ...(layout.positions || {}), [step.id]: position } };
        setLayout(next); api.put(`/api/pipelines/${id}/layout`, next).catch(() => {});
      }
    }
  };

  const reorder = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0) return;
    if (isDraft) {
      setDraftSteps((s) => { const n = [...s]; const [x] = n.splice(from, 1); n.splice(to, 0, x); return n; });
    } else {
      applyPatch([{ kind: 'move_step', src: from, dst: to }]);
    }
  };

  const removeSteps = (ids: string[]) => {
    if (isDraft) setDraftSteps((s) => s.filter((x) => !ids.includes(x.id)));
    else applyPatch(ids.map((sid) => ({ kind: 'remove_step', step_id: sid })));
  };

  const autoLayout = () => {
    const nodes = (activeGraph.nodes || []).map((n: any, i: number) => ({ id: n.id, position: { x: 0, y: i * 104 } }));
    const edges = nodes.slice(1).map((n, i) => ({ source: nodes[i].id, target: n.id }));
    onLayoutChange(dagreLayout(nodes, edges, 'TB'));
  };

  const saveDraft = async () => {
    if (!draftId.trim() || !draftSteps.length) {
      toast('请填写工作项 ID，并至少拖入一个动作', 'warn'); return;
    }
    setSaving(true);
    try {
      const r: any = await api.post('/api/pipelines/draft', {
        id: draftId.trim(), name: draftName.trim() || draftId.trim(), steps: draftSteps,
        workdir: draftWorkdir.trim(), allow_outside_workspace: draftAllowOutside,
      });
      await api.put(`/api/pipelines/${r.id}/layout`, draftLayout);
      toast(`已保存 ${r.id}@${r.version}（${draftSteps.length} 步）`);
      location.hash = `#/editor?id=${r.id}`;
    } catch (e: any) { toast(e.message, 'error'); }
    finally { setSaving(false); }
  };

  const openMeta = async () => {
    try {
      const p: any = await api.get(`/api/pipelines/${id}`);
      setMetaForm({ workdir: p.meta?.workdir || '', allow_outside_workspace: !!p.meta?.allow_outside_workspace });
      setMetaOpen(true);
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const saveMeta = async () => {
    try {
      await api.put(`/api/pipelines/${id}/meta`, metaForm);
      toast('workspace 根 / 越界设置已保存（生成新版本）');
      setMetaOpen(false); await refresh();
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const paramDefs: Record<string, any> = manifest?.params?.properties || {};

  return <div className="page editor-page">
    <div className="page-head"><div><h1>{isDraft ? '编排 · 拖拽新建工作项' : `编排 · ${id}`}</h1>
      <p className="sub">YAML 唯一真源；拖拽动作到画布组合，点击节点编辑参数；图编辑只产生 steps[i] 最小 patch（DR-44/45）</p></div>
      <div className="row">
        {(['both', 'graph', 'yaml'] as const).filter((m) => !isDraft || m !== 'yaml').map((m) => <button key={m}
          className={`btn tiny ${mode === m ? 'primary' : ''}`} onClick={() => setMode(m)}>
          {{ both: '双栏', graph: '仅图', yaml: '仅 YAML' }[m]}</button>)}
        {!isDraft && <>
          <button className="btn tiny" onClick={undo} disabled={!history.length}>撤销 ({history.length})</button>
          <button className="btn tiny" onClick={redo} disabled={!future.length}>重做</button>
          <button id="wfp-save-btn" className="btn tiny" onClick={() => saveYaml(text)}>保存 YAML</button>
          <button className="btn tiny" onClick={openMeta}>路径设置</button>
          <button className="btn tiny" onClick={runLint}>lint</button>
          <button className="btn tiny" onClick={async () => { const d: any = await api.get(`/api/pipelines/${id}/yaml`); setDiffText(d.text); setDiffOpen(true); }}>与上一版 diff</button>
        </>}
        <button className="btn tiny" onClick={autoLayout}>dagre</button>
        {isDraft && <button className="btn primary" disabled={saving || !draftId.trim() || !draftSteps.length}
          onClick={saveDraft}>{saving ? '保存中…' : '保存工作项'}</button>}
      </div></div>

    {isDraft && <div className="card draft-form">
      <label>工作项 ID<br /><input className="input" value={draftId} placeholder="如 my-flow（小写字母/数字/下划线/连字符）"
        onChange={(e) => setDraftId(e.target.value)} /></label>
      <label>名称<br /><input className="input" value={draftName} placeholder="留空则使用 ID"
        onChange={(e) => setDraftName(e.target.value)} /></label>
      <label>workspace 根（可选，建议填 Windows 绝对路径）<br />
        <input className="input" value={draftWorkdir} placeholder="C:/Users/Administrator/Downloads/my_dev"
          onChange={(e) => setDraftWorkdir(e.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={draftAllowOutside}
        onChange={(e) => setDraftAllowOutside(e.target.checked)} />
        允许访问 workspace 之外的绝对路径（DR-24）</label>
      <div className="tiny muted draft-hint">从左侧动作面板拖入动作 → 点击节点编辑参数 → 连线/拖动改序 → 保存工作项。
        相对路径基于 workspace 根；绝对路径与它不在同一盘符时，请把该目录设为 workspace 根或勾选允许越界。</div>
    </div>}

    <div className={`grid cols3 ${mode === 'both' ? '' : 'single'}`}>
      {mode !== 'yaml' && <Section title="动作面板（拖拽到画布 / 双击插入）" className="palette">
        {actions.map((a) => <div key={a.id} className="palette-item" draggable
          onDragStart={(e) => e.dataTransfer.setData('application/wfp-action', `${a.id}@${a.latest_version}`)}
          onDoubleClick={() => insertStep(`${a.id}@${a.latest_version}`)}>
          <b className="mono">{a.id}</b><div className="tiny muted">{a.name} · {a.runtime}</div>
        </div>)}
        <div className="divider" />
        <h3>步骤参数（点击画布节点编辑）</h3>
        {!selectedId ? <Empty text="点击画布中的动作节点，编辑该动作的参数" /> : <>
          <p className="tiny mono">{selectedId} · {manifest?.id || ''}</p>
          <button className="btn primary tiny" onClick={() => setParamsOpen(true)}>编辑参数（可用列表 / 默认值 / 任意参数）</button>
          <div className="divider" />
          <h3>可用参数列表</h3>
          {!Object.keys(paramDefs).length ? <p className="tiny muted">该动作未声明 params，可添加任意参数。</p> :
            <ul className="tiny param-list">{Object.entries<any>(paramDefs).map(([k, d]) => <li key={k}>
              <b className="mono">{k}</b> · {d?.type || 'any'} · 默认 {String(d?.default ?? '—')}
              {d?.description ? ` · ${d.description}` : ''}</li>)}</ul>}
          <SchemaForm schema={manifest?.params || { type: 'object', properties: {} }}
            formData={stepParams} onChange={onParamChange} />
          {!isDraft && <div className="row">
            <button className="btn tiny" onClick={() => applyPatch([{ kind: 'wrap_container', step_id: selectedId, container: 'when', expr: "inputs.flag == true" }])}>加 when 分支</button>
            <button className="btn tiny" onClick={() => applyPatch([{ kind: 'wrap_container', step_id: selectedId, container: 'loop', expr: '[1,2,3]' }])}>加 loop 容器</button>
          </div>}
        </>}
      </Section>}

      {mode !== 'yaml' && <Section title="图视图（React Flow 投影）" className="graph-section">
        <GraphCanvas graph={activeGraph} layout={activeLayout} selectedId={selectedId}
          onInsert={insertStep} onReorder={reorder} onDelete={removeSteps}
          onSelect={selectStep} onLayoutChange={onLayoutChange} />
      </Section>}

      {mode !== 'graph' && <Section title={isDraft ? 'YAML 真源（保存后生成）' : `YAML 真源（Monaco） · sha ${sha.slice(0, 10)}`}
        className="yaml-section">
        {isDraft ? <pre className="pre tiny">{previewYaml(draftId, draftName, draftSteps)}</pre> : <>
          <CodeEditor value={text} height={460} onChange={onYamlChange}
            onMount={(editor) => { editorRef.current = editor; if (lint.length) runLint(); }} />
          <div className="lint-panel">
            <h3>问题面板（lint，点击跳转到 YAML 行列）</h3>
            {!lint.length ? <div className="tiny muted">点击 lint 执行静态校验</div> :
              lint.map((i: any, k: number) => <div key={k} className={`issue ${i.level}`} onClick={() => jumpTo(i.line)}>
                {i.code} · 第 {i.line} 行 · {i.path}：{i.message}</div>)}
          </div>
        </>}
      </Section>}
    </div>

    {paramsOpen && paramStep && <StepParamsModal stepId={paramStep.id} uses={paramStep.uses || ''}
      manifest={manifest} params={paramStep.with || {}} onClose={() => setParamsOpen(false)} onSave={saveParams} />}

    {!isDraft && metaOpen && <Modal title="路径设置（workspace 根 / 越界）" onClose={() => setMetaOpen(false)}>
      <p className="tiny muted">相对路径基于 workspace 根，默认禁止解析到其外。Windows 跨盘符绝对路径
        （如 C:/Users/... 而工作项在 D 盘）需把该目录设为 workspace 根，或显式允许越界（DR-24）。</p>
      <label>workspace 根（绝对路径）<br />
        <input className="input" value={metaForm.workdir} placeholder="C:/Users/Administrator/Downloads/my_dev"
          onChange={(e) => setMetaForm({ ...metaForm, workdir: e.target.value })} /></label>
      <label className="check"><input type="checkbox" checked={metaForm.allow_outside_workspace}
        onChange={(e) => setMetaForm({ ...metaForm, allow_outside_workspace: e.target.checked })} />
        允许访问 workspace 之外的绝对路径</label>
      <div className="row end"><button className="btn" onClick={() => setMetaOpen(false)}>取消</button>
        <button className="btn primary" onClick={saveMeta}>保存设置</button></div>
    </Modal>}

    {!isDraft && diffOpen && <Modal title="YAML 与上一版 diff（Monaco）" wide onClose={() => setDiffOpen(false)}>
      <DiffView before={diffText} after={text} height={420} />
    </Modal>}
  </div>;
}
