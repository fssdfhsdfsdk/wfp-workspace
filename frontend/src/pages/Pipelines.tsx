/* 工作项列表：导入、inputs 表单、干跑/确认闸/执行、复制、导出、删除二次确认。
   Implements: DR-27, DR-28, DR-31, DR-42, DR-49 */
import { useEffect, useState } from 'react';
import { api, downloadFile, pollTask, toast } from '../api';
import { CodeEditor } from '../Monaco';
import { SchemaForm } from '../SchemaForm';
import { Badge, Empty, Modal, NameConfirm, Section } from '../ui';
import { AIDraftModal } from './AIDraft';

export function PipelinesPage() {
  const [list, setList] = useState<any[]>([]);
  const [stats, setStats] = useState<Record<string, any>>({});
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [runFor, setRunFor] = useState<any>(null);
  const [inputs, setInputs] = useState<any>({});
  const [report, setReport] = useState<any>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState<any>(null);
  const [aiOpen, setAiOpen] = useState(false);

  const load = async () => {
    try {
      const [ps, summary]: any = await Promise.all([api.get('/api/pipelines'), api.get('/api/history/summary')]);
      setList(ps);
      const map: any = {};
      (summary.per_run || []).forEach((r: any) => { if (!map[r.pipeline]) map[r.pipeline] = r; });
      setStats(map);
    } catch (e: any) { toast(e.message, 'error'); }
  };
  useEffect(() => { load(); }, []);

  const doImport = async () => {
    try { const r: any = await api.post('/api/pipelines', { yaml: importText }); toast(`已导入 ${r.id}@${r.version}`); setImportOpen(false); await load(); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  const openRun = async (p: any) => {
    try {
      const meta: any = await api.get(`/api/pipelines/${p.id}`);
      setRunFor({ ...p, inputsSchema: meta.inputs || { type: 'object', properties: {} } });
      setInputs({}); setReport(null); setAck(false);
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const plan = async (data: any) => {
    setBusy(true);
    try {
      const t: any = await api.post('/api/runs', { pipeline: runFor.id, inputs: data, phase: 'plan' });
      const r: any = await pollTask(t.task_id);
      setInputs(data); setReport(r);
      localStorage.setItem(`wfp-plan-${r.run_id}`, JSON.stringify(r));
      toast('干跑完成，请查看 PlanReport 后再确认执行（DR-49）');
    } catch (e: any) { toast(e.message, 'error'); }
    finally { setBusy(false); }
  };

  const apply = async () => {
    if (!report) return;
    if (report.confirm_required && !ack) { toast('存在不可补偿/不可预测步骤，必须显式勾选确认', 'warn'); return; }
    setBusy(true);
    try {
      const t: any = await api.post(`/api/runs/${report.run_id}/apply`, { ack_risks: ack });
      const r: any = await pollTask(t.task_id);
      toast(`执行完成：${r.status}`);
      location.hash = `#/runs?id=${r.id}`;
    } catch (e: any) { toast(e.message, 'error'); }
    finally { setBusy(false); }
  };

  const reportItem = (i: any) => <div className={`issue ${i.unpredictable ? 'error' : ''}`} key={i.step_id}>
    <b>{i.step_id}</b> <span className="mono">{i.action_ref}</span> <Badge status={i.status} />
    <div className="tiny muted">目标：{(i.targets || []).join('，').slice(0, 220)}</div>
    {i.diff && <pre className="pre tiny">{i.diff.slice(0, 900)}</pre>}
    {(i.risks || []).map((r: string, k: number) => <div className="tiny warn-text" key={k}>⚠ {r}</div>)}
    {i.compensate !== 'supported' && <div className="tiny warn-text">补偿：{i.compensate}</div>}
    {i.no_backup_reason && <div className="tiny muted">备份说明：{i.no_backup_reason}</div>}
  </div>;

  return <div className="page">
    <div className="page-head"><div><h1>工作项列表</h1><p className="sub">YAML 真源；一键运行先弹 inputs 表单，干跑预览 → 确认闸 → 执行（DR-42/49）</p></div>
      <div className="row"><button className="btn primary" onClick={() => setImportOpen(true)}>导入 YAML</button>
        <button className="btn" onClick={() => setAiOpen(true)}>AI 草案</button>
        <button className="btn" onClick={load}>刷新</button></div></div>
    <Section title={`工作项（${list.length}）`}>
      {!list.length ? <Empty text="还没有工作项，先导入一个 YAML。" /> :
        <table><thead><tr><th>ID</th><th>名称</th><th>版本</th><th>步骤</th><th>最近运行</th><th>耗时</th><th>操作</th></tr></thead>
          <tbody>{list.map((p) => {
            const s = stats[p.id];
            return <tr key={p.id}>
              <td className="mono">{p.id}</td><td>{p.name}</td><td>{p.latest_version}</td>
              <td>{p.step_count || s?.steps || '-'}</td>
              <td>{s ? <Badge status={s.status} /> : '-'}</td><td>{s ? `${s.duration_ms}ms` : '-'}</td>
              <td><div className="row">
                <button className="btn tiny primary" onClick={() => openRun(p)}>运行</button>
                <button className="btn tiny" onClick={() => location.hash = `#/editor?id=${p.id}`}>编排</button>
                <button className="btn tiny" onClick={() => {
                  const id = prompt('复制为新工作项 ID：', `${p.id}-copy`);
                  if (id) api.post(`/api/pipelines/${p.id}/duplicate`, { new_id: id }).then(() => { toast('已复制'); load(); }).catch((e) => toast(e.message, 'error'));
                }}>复制</button>
                <button className="btn tiny" onClick={() => downloadFile(`/api/pipelines/${p.id}/download`, `${p.id}.zip`).catch((e) => toast(e.message, 'error'))}>导出</button>
                <button className="btn tiny danger" onClick={() => setConfirmDel(p)}>删除</button>
              </div></td>
            </tr>;
          })}</tbody></table>}
    </Section>

    {importOpen && <Modal title="导入工作项 YAML（Monaco）" wide onClose={() => setImportOpen(false)}>
      <CodeEditor value={importText} height={360} onChange={setImportText} />
      <div className="row end"><button className="btn primary" onClick={doImport}>导入为真源</button></div>
    </Modal>}

    {runFor && <Modal title={`运行 ${runFor.id} · inputs / PlanReport / 确认闸`} wide onClose={() => setRunFor(null)}>
      {!report ? <div>
        <p className="muted">输入快照会写入 Run 记录，可用于复现（DR-27）。</p>
        <SchemaForm schema={runFor.inputsSchema} formData={inputs} onChange={setInputs} onSubmit={plan}
          submitId="wfp-plan-btn" submitLabel={busy ? '干跑中…' : '干跑（零副作用）'} />
      </div> : <div>
        <div className="row"><b>PlanReport</b><span className="mono">{report.run_id}</span>
          <Badge status={report.confirm_required ? '需要确认' : 'planned'} />
          <span className="tiny muted">sha256 {String(report.sha256).slice(0, 16)}…</span></div>
        <p className="tiny muted">{report.items.length} 步 · {report.risks.length} 项风险 · lint {report.lint.length} 条</p>
        <div className="report-scroll">{report.items.map(reportItem)}</div>
        <label className="check"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          我已查看 diff / 风险 / 不可补偿项，确认执行（DR-49）</label>
        <div className="row end"><button className="btn" onClick={() => setReport(null)}>返回修改 inputs</button>
          <button id="wfp-apply-btn" className="btn primary" disabled={busy} onClick={apply}>确认执行</button></div>
      </div>}
    </Modal>}

    {confirmDel && <Modal title="删除工作项" onClose={() => setConfirmDel(null)}>
      <NameConfirm name={confirmDel.id} onCancel={() => setConfirmDel(null)} onConfirm={async () => {
        try { await api.del(`/api/pipelines/${confirmDel.id}`); toast('已删除'); setConfirmDel(null); await load(); }
        catch (e: any) { toast(e.message, 'error'); }
      }} />
    </Modal>}
    {aiOpen && <AIDraftModal onClose={() => setAiOpen(false)} onImported={load} />}
  </div>;
}
