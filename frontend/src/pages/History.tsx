/* 历史与报表：筛选、跨 run 汇总、以相同输入与版本重跑、md/html/csv 导出。
   Implements: DR-65, DR-66, DR-78 */
import { useEffect, useMemo, useState } from 'react';
import { api, downloadFile, toast } from '../api';
import { Badge, Empty, Section } from '../ui';

export function HistoryPage() {
  const [summary, setSummary] = useState<any>({ runs: 0, per_run: [], by_status: {}, success_rate: 0 });
  const [pipelines, setPipelines] = useState<any[]>([]);
  const [pipeline, setPipeline] = useState('');
  const [status, setStatus] = useState('');
  const [keyword, setKeyword] = useState('');

  const load = async () => {
    try {
      const [s, p]: any = await Promise.all([api.get('/api/history/summary'), api.get('/api/pipelines')]);
      setSummary(s); setPipelines(p);
    } catch (e: any) { toast(e.message, 'error'); }
  };
  useEffect(() => { load(); }, []);

  const rows = useMemo(() => (summary.per_run || []).filter((r: any) =>
    (!pipeline || r.pipeline === pipeline) && (!status || r.status === status) &&
    (!keyword || `${r.run_id} ${r.pipeline}`.toLowerCase().includes(keyword.toLowerCase()))),
  [summary, pipeline, status, keyword]);

  const replay = async (runId: string) => {
    try { const r: any = await api.post(`/api/runs/${runId}/replay`); toast(`已用原输入/版本重跑：${r.status}`); await load(); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  return <div className="page">
    <div className="page-head"><div><h1>历史与报表</h1><p className="sub">跨 run 汇总、幂等跳过率、失败分布、一键复现与导出（DR-65/66）</p></div>
      <div className="row"><button className="btn" onClick={load}>刷新</button></div></div>
    <div className="grid cols3 stats-grid">
      <Section title="总运行数"><div className="stat">{summary.runs}</div></Section>
      <Section title="成功率"><div className="stat">{summary.success_rate}%</div></Section>
      <Section title="状态分布">{Object.entries(summary.by_status || {}).map(([k, v]) =>
        <div className="issue" key={k}>{k}：{String(v)}</div>)}</Section>
    </div>
    <Section title="筛选与运行明细">
      <div className="row filters">
        <input className="input" placeholder="搜索 run / 工作项" value={keyword} onChange={(e) => setKeyword(e.target.value)} />
        <select className="input narrow" value={pipeline} onChange={(e) => setPipeline(e.target.value)}>
          <option value="">全部工作项</option>{pipelines.map((p) => <option key={p.id} value={p.id}>{p.id}</option>)}</select>
        <select className="input narrow" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>{['succeeded', 'failed', 'cancelled', 'compensated', 'needs_attention', 'planned'].map((s) => <option key={s}>{s}</option>)}</select>
      </div>
      {!rows.length ? <Empty text="没有匹配的运行记录" /> : <table><thead><tr>
        <th>Run</th><th>工作项</th><th>状态</th><th>步骤</th><th>幂等跳过率</th><th>重试</th><th>耗时</th><th>操作</th></tr></thead>
        <tbody>{rows.map((r: any) => <tr key={r.run_id}>
          <td className="mono">{r.run_id}</td><td>{r.pipeline}@{r.version}</td><td><Badge status={r.status} /></td>
          <td>{r.steps}</td><td>{r.skip_rate}%</td><td>{r.retries}</td><td>{r.duration_ms}ms</td>
          <td><div className="row"><button className="btn tiny" onClick={() => replay(r.run_id)}>相同输入重跑</button>
            {['md', 'html', 'csv'].map((f) => <button key={f} className="btn tiny"
              onClick={() => downloadFile(`/api/runs/${r.run_id}/report?fmt=${f}`, `${r.run_id}.${f}`).catch((e) => toast(e.message, 'error'))}>{f.toUpperCase()}</button>)}
            <button className="btn tiny" onClick={() => location.hash = `#/runs?id=${r.run_id}`}>查看面板</button>
          </div></td>
        </tr>)}</tbody></table>}
    </Section>
  </div>;
}
