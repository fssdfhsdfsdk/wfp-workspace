/* 步骤参数编辑器：可用参数列表、修改默认参数、任意参数（DR-14/27/48）。
   编排视图点击节点后打开，保存只产生 steps[i].with 的最小 patch。 */
import { useEffect, useState } from 'react';
import { Modal } from './ui';

function show(v: any): string {
  if (v === undefined || v === null) return '';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

function parse(raw: string): any {
  const t = raw.trim();
  if (!t) return '';
  if (t.startsWith('{') || t.startsWith('[') || t === 'true' || t === 'false'
      || t === 'null' || /^-?\d+(\.\d+)?$/.test(t)) {
    try { return JSON.parse(t); } catch { return raw; }
  }
  return raw;
}

type Row = { key: string; value: string };

export function StepParamsModal(props: {
  stepId: string; uses: string; manifest: any; params: any;
  onClose: () => void; onSave: (next: any) => void;
}) {
  const { stepId, uses, manifest, params, onClose, onSave } = props;
  const schema = manifest?.params || { type: 'object', properties: {} };
  const defs: Record<string, any> = schema.properties || {};
  const required: string[] = schema.required || [];
  const [rows, setRows] = useState<Row[]>([]);
  const [newKey, setNewKey] = useState('');

  useEffect(() => {
    const merged: any = { ...(params || {}) };
    Object.entries<any>(defs).forEach(([k, d]) => {
      if (!(k in merged) && d && 'default' in d) merged[k] = d.default;
    });
    setRows(Object.keys(merged).map((k) => ({ key: k, value: show(merged[k]) })));
    setNewKey('');
    // 仅在切换步骤时重建编辑区，避免输入过程中被父组件刷新覆盖
  }, [stepId]);

  const addKey = (key: string) => {
    if (!key) return;
    setRows((r) => r.some((x) => x.key === key)
      ? r : [...r, { key, value: show(defs[key]?.default ?? '') }]);
  };
  const update = (i: number, patch: Partial<Row>) =>
    setRows((r) => r.map((x, k) => (k === i ? { ...x, ...patch } : x)));

  const save = () => {
    const next: any = {};
    rows.forEach((r) => { if (r.key.trim()) next[r.key.trim()] = parse(r.value); });
    onSave(next);
  };

  return <Modal title={`编辑动作参数 · ${stepId}（${uses}）`} wide onClose={onClose}>
    <h3>可用参数列表（manifest.params）</h3>
    <p className="tiny muted">点击「加入」放进下方编辑区；默认值可直接修改并覆盖到 with；任意参数不受列表限制。</p>
    <table className="param-table"><thead><tr>
      <th>参数</th><th>类型 / 说明</th><th>默认值</th><th>操作</th>
    </tr></thead><tbody>
      {Object.entries<any>(defs).map(([k, d]) => <tr key={k}>
        <td className="mono">{k}{required.includes(k) ? <span className="badge warn">必填</span> : null}</td>
        <td className="tiny">{d?.type || (d?.enum ? 'enum' : 'any')}
          {Array.isArray(d?.enum) ? ` · ${d.enum.join(' / ')}` : ''} {d?.description || ''}</td>
        <td className="mono tiny">{show(d?.default)}</td>
        <td><div className="row">
          <button className="btn tiny" onClick={() => addKey(k)}>加入</button>
          {d && 'default' in d ? <button className="btn tiny" onClick={() => {
            const i = rows.findIndex((r) => r.key === k);
            if (i >= 0) update(i, { value: show(d.default) }); else addKey(k);
          }}>恢复默认</button> : null}
        </div></td>
      </tr>)}
    </tbody></table>
    {!Object.keys(defs).length ? <p className="tiny muted">该动作未声明 params，可直接添加任意参数。</p> : null}

    <div className="divider" />
    <h3>已设置参数（修改默认参数 / 任意参数）</h3>
    {rows.map((r, i) => <div className="param-row" key={`${r.key}-${i}`}>
      <input className="input" value={r.key} placeholder="参数名（任意）"
        onChange={(e) => update(i, { key: e.target.value })} />
      <input className="input" value={r.value}
        placeholder={'字符串，或 JSON：true / 3 / [1,2] / {"a":1}'}
        onChange={(e) => update(i, { value: e.target.value })} />
      <button className="btn tiny danger" onClick={() => setRows((x) => x.filter((_, k) => k !== i))}>删除</button>
    </div>)}
    {!rows.length ? <p className="tiny muted">还没有参数，从上方列表加入或直接添加任意参数。</p> : null}
    <div className="row">
      <input className="input" value={newKey} placeholder="自定义参数名" onChange={(e) => setNewKey(e.target.value)} />
      <button className="btn tiny" onClick={() => { addKey(newKey.trim()); setNewKey(''); }}>添加任意参数</button>
    </div>
    <div className="row end">
      <button className="btn" onClick={onClose}>取消</button>
      <button className="btn primary" onClick={save}>保存参数到 steps[i].with</button>
    </div>
  </Modal>;
}
