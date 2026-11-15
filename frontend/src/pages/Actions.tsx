/* 动作库：分类/搜索、CRUD、实现体 Monaco 编辑、版本 diff、契约套件、单动作试跑。
   Implements: DR-03, DR-10, DR-11, DR-13, DR-17, DR-18, DR-19, DR-41, DR-75 */
import { useEffect, useState } from 'react';
import { api, toast } from '../api';
import { CodeEditor, DiffView } from '../Monaco';
import { SchemaForm } from '../SchemaForm';
import { Badge, Empty, Modal, NameConfirm, Section } from '../ui';

const META_SCHEMA: any = {
  type: 'object', required: ['id', 'name', 'version', 'runtime', 'category'],
  properties: {
    id: { type: 'string', title: '动作 ID', pattern: '^[a-z][a-z0-9_.]*$' },
    name: { type: 'string', title: '名称' },
    version: { type: 'string', title: '版本', default: '1.0.0' },
    description: { type: 'string', title: '描述' },
    category: { type: 'string', title: '分类', enum: ['archive', 'fs', 'config', 'net', 'assert', 'script', 'remote'] },
    tags: { type: 'array', title: '标签', items: { type: 'string' } },
    runtime: { type: 'string', title: 'runtime', enum: ['builtin', 'python', 'powershell', 'cmd', 'remote'], default: 'builtin' },
    dry_run: { type: 'string', title: 'dry_run', enum: ['plan', 'none'], default: 'plan' },
    idempotent: { type: 'string', title: '幂等', enum: ['yes', 'conditional', 'no'], default: 'yes' },
    compensate: { type: 'string', title: '补偿', enum: ['supported', 'manual', 'none'], default: 'supported' },
  },
};

function assemble(meta: any, paramsJson: string): any {
  const runtime = meta.runtime || 'builtin';
  const entry: any = runtime === 'builtin' ? { module: 'wfp.actions.builtin:dispatch' }
    : runtime === 'remote' ? { operation: 'exec', transport: 'auto' }
      : runtime === 'python' ? { script: 'script.py', argv: [], callable: 'main' }
        : { command: 'echo hello' };
  const caps = {
    dry_run: meta.dry_run || 'plan', idempotent: meta.idempotent || 'yes',
    compensate: meta.compensate || 'supported', needs_admin: false,
    needs_network: runtime === 'remote', side_effects: runtime === 'remote' ? 'remote_exec' : 'local_fs',
  };
  const m: any = {
    id: meta.id, version: meta.version || '1.0.0', name: meta.name,
    description: meta.description || '', category: meta.category || 'script',
    tags: meta.tags || [], runtime, entry, params: JSON.parse(paramsJson || '{}'),
    outputs: { type: 'object', properties: {} }, capabilities: caps,
    policy: { timeout_seconds: 300, console: 'hidden', stdin: 'closed' },
    evidence: ['report'], ui: {},
  };
  if (caps.dry_run === 'plan') m.plan = { entry: {} };
  if (caps.compensate === 'supported') m.compensate = { entry: {} };
  return m;
}

export function ActionsPage() {
  const [list, setList] = useState<any[]>([]);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [selected, setSelected] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [impl, setImpl] = useState('');
  const [tab, setTab] = useState('edit');
  const [versions, setVersions] = useState<any[]>([]);
  const [diff, setDiff] = useState<any>(null);
  const [suite, setSuite] = useState<any>(null);
  const [refs, setRefs] = useState<any[]>([]);
  const [trial, setTrial] = useState<any>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createMeta, setCreateMeta] = useState<any>({});
  const [createParams, setCreateParams] = useState('{"type":"object","properties":{},"required":[]}');
  const [createImpl, setCreateImpl] = useState('print("WFP_OUTPUT::{}")\n');
  const [confirmDelete, setConfirmDelete] = useState<any>(null);

  const load = () => api.get(`/api/actions?query=${encodeURIComponent(query)}&category=${encodeURIComponent(category)}`)
    .then(setList).catch((e) => toast(e.message, 'error'));
  useEffect(() => { load(); }, [query, category]);

  const open = async (id: string) => {
    try {
      const a: any = await api.get(`/api/actions/${id}`);
      const v: any = await api.get(`/api/actions/${id}/versions/${a.latest_version}`);
      const vs: any = await api.get(`/api/actions/${id}/versions`);
      const s: any = await api.post(`/api/actions/${id}/test`);
      const r: any = await api.get(`/api/actions/${id}/references`);
      setSelected(a); setDetail(v); setImpl(v.impl || ''); setVersions(vs); setSuite(s); setRefs(r);
      setDiff(null); setTrial(null); setTab('edit');
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const saveVersion = async () => {
    try {
      const m = { ...detail.manifest };
      const parts = m.version.split('.');
      m.version = `${parts[0]}.${parts[1]}.${Number(parts[2]) + 1}`;
      await api.put(`/api/actions/${selected.id}`, { manifest: m, impl });
      toast(`已生成新版本 ${m.version}`); await open(selected.id); await load();
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const create = async () => {
    try {
      const m = assemble(createMeta, createParams);
      await api.post('/api/actions', { manifest: m, impl: createImpl });
      toast(`动作 ${m.id} 已创建`); setCreateOpen(false); await load();
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const showDiff = async (from: string, to: string) => {
    try { setDiff(await api.get(`/api/actions/${selected.id}/diff?from_version=${from}&to_version=${to}`)); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  const runTrial = async (params: any, phase: string) => {
    try {
      const r = await api.post(`/api/actions/${selected.id}/trial`, { params, phase });
      setTrial(r);
    } catch (e: any) { toast(e.message, 'error'); }
  };

  return <div className="page">
    <div className="page-head"><div><h1>动作库</h1><p className="sub">manifest + 实现体，版本不可变；分类/搜索/能力徽章/单动作试跑（DR-41）</p></div>
      <div className="row"><button className="btn primary" onClick={() => setCreateOpen(true)}>新建动作</button>
        <button className="btn" onClick={load}>刷新</button></div></div>
    <div className="grid cols2 page-grid">
      <Section title={`全部动作（${list.length}）`}>
        <div className="row filters">
          <input className="input" placeholder="搜索名称/ID/标签" value={query} onChange={(e) => setQuery(e.target.value)} />
          <select className="input narrow" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">全部分类</option>
            {['archive', 'fs', 'config', 'net', 'assert', 'script', 'remote'].map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        {!list.length ? <Empty text="没有匹配的动作" /> :
          <table><thead><tr><th>ID</th><th>名称</th><th>runtime</th><th>能力</th><th>状态</th><th></th></tr></thead>
            <tbody>{list.map((a) => <tr key={a.id} className={selected?.id === a.id ? 'row-active' : ''}>
              <td className="mono">{a.id}</td><td>{a.name}</td><td>{a.runtime}</td>
              <td><span className="badge ok">dry:{a.badges?.dry_run}</span>
                <span className="badge">幂等:{a.badges?.idempotent}</span>
                <span className={`badge ${a.badges?.compensate === 'supported' ? 'ok' : 'warn'}`}>补偿:{a.badges?.compensate}</span></td>
              <td><Badge status={a.enabled ? 'enabled' : 'disabled'} /></td>
              <td><button className="btn tiny" onClick={() => open(a.id)}>查看/编辑</button></td>
            </tr>)}</tbody></table>}
      </Section>

      <Section title={selected ? `${selected.id}@${selected.latest_version}` : '动作详情'} className="detail-card">
        {!selected ? <Empty text="从左侧选择一个动作，查看 manifest、实现体、版本 diff 与契约套件。" /> : <>
          <div className="tabs">
            {['edit', 'versions', 'suite', 'trial', 'refs'].map((t) =>
              <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
                {{ edit: '实现体编辑', versions: '版本/diff', suite: '契约套件', trial: '单动作试跑', refs: '引用' }[t]}
              </button>)}
          </div>
          {tab === 'edit' && <div>
            <label>manifest（只读，新建时可表单编辑）</label>
            <CodeEditor value={JSON.stringify(detail.manifest, null, 2)} language="json" height={210} readOnly />
            <label>实现体查看/编辑窗口（Monaco，保存生成新版本）</label>
            <CodeEditor value={impl} language={detail.manifest.runtime === 'python' ? 'python' : 'yaml'} height={260} onChange={setImpl} />
            <div className="row end">
              <button className="btn" onClick={() => api.post(`/api/actions/${selected.id}/enable`, { enabled: !selected.enabled }).then(() => { toast('状态已更新'); open(selected.id); })}>
                {selected.enabled ? '停用' : '启用'}</button>
              <button className="btn" onClick={() => {
                const id = prompt('复制为新动作 ID：', `${selected.id}.copy`);
                if (id) api.post(`/api/actions/${selected.id}/duplicate`, { new_id: id }).then(() => { toast('已复制'); load(); });
              }}>复制</button>
              <button className="btn danger" onClick={() => setConfirmDelete(selected)}>删除</button>
              <button className="btn primary" onClick={saveVersion}>保存为新版本</button>
            </div>
          </div>}
          {tab === 'versions' && <div>
            <table><thead><tr><th>版本</th><th>runtime</th><th>impl sha</th><th>diff</th></tr></thead>
              <tbody>{versions.map((v, i) => <tr key={v.version}><td>{v.version}</td><td>{v.runtime}</td>
                <td className="mono">{v.impl_sha256?.slice(0, 12)}</td>
                <td>{i > 0 && <button className="btn tiny" onClick={() => showDiff(versions[i - 1].version, v.version)}>与上一版 diff</button>}</td>
              </tr>)}</tbody></table>
            {diff && <div><label>实现体行级 diff</label><DiffView before={diff.before || ''} after={diff.after || ''} language="python" height={280} /></div>}
          </div>}
          {tab === 'suite' && <div>
            <p>契约一致性套件：{suite?.passed}/{suite?.total} 通过（DR-18）</p>
            {(suite?.checks || []).map((c: any) => <div key={c.name} className={`issue ${c.ok ? '' : 'error'}`}>
              {c.ok ? '✓' : '✗'} {c.name}：{c.detail}</div>)}
          </div>}
          {tab === 'trial' && <div>
            <p className="muted">用 manifest.params 生成参数表单，单动作 plan（零副作用）或 apply（DR-41）。</p>
            <SchemaForm schema={detail.manifest.params} onSubmit={(data) => runTrial(data, 'plan')} submitLabel="单动作干跑" />
            <button className="btn" onClick={() => runTrial({}, 'apply')}>以空参数试跑 apply</button>
            {trial && <pre className="pre">{JSON.stringify(trial, null, 2)}</pre>}
          </div>}
          {tab === 'refs' && <div>
            {refs.length ? refs.map((r, i) => <div className="issue" key={i}>{r.pipeline_id}@{r.pipeline_version} ← {r.action_version}</div>)
              : <Empty text="没有被工作项引用" />}
          </div>}
        </>}
      </Section>
    </div>

    {createOpen && <Modal title="新建动作（manifest 表单 + Monaco 实现体）" wide onClose={() => setCreateOpen(false)}>
      <SchemaForm schema={META_SCHEMA} formData={createMeta} onChange={setCreateMeta} />
      <label>params JSON Schema（Monaco）</label>
      <CodeEditor value={createParams} language="json" height={160} onChange={setCreateParams} />
      <label>实现体（Monaco）</label>
      <CodeEditor value={createImpl} language="python" height={160} onChange={setCreateImpl} />
      <div className="row end"><button className="btn primary" onClick={create}>创建并入库</button></div>
    </Modal>}
    {confirmDelete && <Modal title="删除动作" onClose={() => setConfirmDelete(null)}>
      <NameConfirm name={confirmDelete.id} onCancel={() => setConfirmDelete(null)} onConfirm={async () => {
        try { await api.del(`/api/actions/${confirmDelete.id}`); toast('已软删除'); setConfirmDelete(null); setSelected(null); await load(); }
        catch (e: any) { toast(e.message, 'error'); }
      }} />
    </Modal>}
  </div>;
}
