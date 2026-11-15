/* 节点与凭据：节点 CRUD（ip/用户名/密码/描述/标签）、当前目标节点、连接测试、凭据加密管理。
   Implements: DR-50, DR-51, DR-52, DR-53, DR-58, DR-68 */
import { useEffect, useState } from 'react';
import { api, toast } from '../api';
import { Badge, Empty, Modal, Section } from '../ui';

const EMPTY_NODE: any = { id: '', name: '', host: '', port: 22, username: 'root', os_kind: 'ssh-linux',
  auth_kind: 'password', credential_id: '', description: '', tags: [], default_workdir: '/' };

export function NodesPage() {
  const [nodes, setNodes] = useState<any[]>([]);
  const [creds, setCreds] = useState<any[]>([]);
  const [current, setCurrent] = useState('');
  const [form, setForm] = useState<any>({ ...EMPTY_NODE });
  const [editing, setEditing] = useState(false);
  const [credLabel, setCredLabel] = useState('');
  const [credValue, setCredValue] = useState('');
  const [probe, setProbe] = useState<Record<string, any>>({});

  const load = async () => {
    try {
      const [n, c, cur]: any = await Promise.all([api.get('/api/nodes'), api.get('/api/credentials'), api.get('/api/context/current-node')]);
      setNodes(n); setCreds(c); setCurrent(cur.current_node || '');
    } catch (e: any) { toast(e.message, 'error'); }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    try {
      if (editing) await api.put(`/api/nodes/${form.id}`, form);
      else await api.post('/api/nodes', form);
      toast('节点已保存'); setForm({ ...EMPTY_NODE }); setEditing(false); await load();
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const edit = (n: any) => { setForm({ ...EMPTY_NODE, ...n, tags: n.tags || [] }); setEditing(true); };

  const addCred = async () => {
    try { await api.post('/api/credentials', { label: credLabel, value: credValue }); toast('凭据已加密保存（明文不入库/不返回）'); setCredLabel(''); setCredValue(''); await load(); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  const setAsCurrent = async (id: string | null) => {
    try { await api.put('/api/context/current-node', { node_id: id }); setCurrent(id || ''); toast('当前目标节点已更新（运行快照不受影响，DR-51）'); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  const doProbe = async (id: string) => {
    try { const r: any = await api.post(`/api/nodes/${id}/probe`); setProbe((p) => ({ ...p, [id]: r })); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  return <div className="page">
    <div className="page-head"><div><h1>节点与凭据</h1><p className="sub">远程节点 ip/用户名/密码/描述；全局单选"当前目标节点"；凭据加密存储（DR-50/52）</p></div>
      <div className="row"><button className="btn" onClick={load}>刷新</button></div></div>
    <div className="grid cols2 page-grid">
      <Section title={`远程节点（${nodes.length}）`} actions={<span className="chip">当前：{current || '未选择'}</span>}>
        {!nodes.length ? <Empty text="还没有节点" /> : <table><thead><tr><th>ID</th><th>主机</th><th>OS</th><th>描述</th><th>操作</th></tr></thead>
          <tbody>{nodes.map((n) => <tr key={n.id}>
            <td className="mono">{n.id}{current === n.id && <span className="badge ok">★</span>}</td>
            <td>{n.host}:{n.port}<div className="tiny muted">{n.username}</div></td><td>{n.os_kind}</td>
            <td>{n.description || '-'}<div className="tiny muted">{(n.tags || []).join(', ')}</div></td>
            <td><div className="row">
              <button className="btn tiny" onClick={() => setAsCurrent(n.id)}>设为当前</button>
              <button className="btn tiny" onClick={() => doProbe(n.id)}>测试连接</button>
              <button className="btn tiny" onClick={() => edit(n)}>编辑</button>
              <button className="btn tiny danger" onClick={() => api.del(`/api/nodes/${n.id}`).then(() => { toast('已删除'); load(); })}>删除</button>
            </div>{probe[n.id] && <div className="tiny">{probe[n.id].ok ? `✓ ${probe[n.id].rtt_ms}ms` : `✗ ${probe[n.id].error_code}`}</div>}</td>
          </tr>)}</tbody></table>}
        <button className="btn tiny" onClick={() => setAsCurrent(null)}>清空当前节点</button>
      </Section>

      <Section title={editing ? `编辑节点 ${form.id}` : '新增节点 / 凭据'}>
        <div className="form-grid">
          {[['id', 'ID'], ['name', '名称'], ['host', 'IP/主机'], ['username', '用户名'], ['description', '描述'],
            ['default_workdir', '默认工作目录'], ['tags', '标签（逗号分隔）']].map(([k, label]) =>
            <label key={k}>{label}<input className="input" disabled={editing && k === 'id'}
              value={k === 'tags' ? (form.tags || []).join(',') : (form[k] || '')}
              onChange={(e) => setForm({ ...form, [k]: k === 'tags' ? e.target.value.split(',').map((x) => x.trim()).filter(Boolean) : e.target.value })} /></label>)}
          <label>端口<input className="input" type="number" value={form.port || 22} onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} /></label>
          <label>OS<select className="input" value={form.os_kind} onChange={(e) => setForm({ ...form, os_kind: e.target.value })}>
            {['ssh-linux', 'ssh-windows', 'winrm'].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>认证方式<select className="input" value={form.auth_kind} onChange={(e) => setForm({ ...form, auth_kind: e.target.value })}>
            {['password', 'key', 'agent'].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>凭据引用<select className="input" value={form.credential_id || ''} onChange={(e) => setForm({ ...form, credential_id: e.target.value })}>
            <option value="">（无）</option>{creds.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select></label>
        </div>
        <div className="row end"><button className="btn" onClick={() => { setForm({ ...EMPTY_NODE }); setEditing(false); }}>重置</button>
          <button className="btn primary" onClick={save}>{editing ? '保存修改' : '添加节点'}</button></div>
        <div className="divider" />
        <h3>凭据（{creds.length}）</h3>
        <div className="row"><input className="input" placeholder="标签，如 prod.root" value={credLabel} onChange={(e) => setCredLabel(e.target.value)} />
          <input className="input" type="password" placeholder="值（仅内存加密）" value={credValue} onChange={(e) => setCredValue(e.target.value)} />
          <button className="btn primary" onClick={addCred}>加密保存</button></div>
        {creds.map((c) => <div className="issue" key={c.id}>
          <Badge status={c.scheme} /> <b>{c.label}</b> <span className="tiny muted">{c.kind}</span>
          <button className="btn tiny danger" onClick={() => api.del(`/api/credentials/${c.id}`).then(() => { toast('已删除'); load(); })}>删除</button>
        </div>)}
      </Section>
    </div>
  </div>;
}
