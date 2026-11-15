/* 运行面板：步骤树 + SSE 日志流（过滤/搜索/暂停/ANSI/下载）+ 输出查看窗口 +
   证据 diff/zip + 运行控制 + 报表。
   Implements: DR-38, DR-60, DR-61, DR-62, DR-63, DR-64, DR-66, DR-77 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, downloadFile, sseUrl, toast } from '../api';
import { DiffView } from '../Monaco';
import { Badge, Empty, Modal, NameConfirm, Section } from '../ui';

const ANSI = /\x1b\[[0-9;]*m/g;
const TERMINAL = new Set(['succeeded', 'succeeded_with_warnings', 'failed', 'cancelled',
  'planned', 'plan_failed', 'interrupted', 'compensated', 'manual_attention']);
const stamp = (v: any) => String(v || '').replace('T', ' ').slice(0, 19);

const payloadLine = (ev: any): string => {
  const p = ev.payload || {};
  if (ev.type === 'step.started') {
    return [
      `动作 ${p.action || '-'}`, `runtime ${p.runtime || '-'}`,
      `命令/入口 ${p.command || p.entry || '-'}`,
      `参数 ${JSON.stringify(p.params || {})}`,
      `目标 ${(p.targets || []).join(', ') || '-'}`,
      `工作目录 ${p.workdir || '-'}`,
      p.info?.output ? `输出 ${p.info.output}` : '',
      p.info?.archive_root ? `包内根 ${p.info.archive_root}` : '',
    ].filter(Boolean).join('  |  ');
  }
  if (ev.type === 'step.attempt') {
    return `动作 ${p.action || '-'}  |  第 ${p.attempt || 1} 次尝试`;
  }
  if (ev.type === 'step.stdout' || ev.type === 'step.stderr') {
    return String(p.text ?? JSON.stringify(p));
  }
  if (ev.type === 'step.finished') {
    return [
      `状态 ${p.status || '-'}`,
      `结果 ${JSON.stringify(p.outputs ?? p.result ?? p.error ?? {})}`,
      `耗时 ${p.duration_ms ?? '-'}ms`, `attempt ${p.attempt ?? '-'}`,
      p.receipts?.length ? `证据 ${p.receipts.length} 项` : '',
    ].filter(Boolean).join('  |  ');
  }
  if (ev.type === 'step.skipped') {
    return `原因 ${p.reason || '-'}  |  ${p.note || ''}  |  key ${p.key || '-'}`;
  }
  if (ev.type === 'run.started') {
    return `阶段 ${p.phase || '-'}  |  工作项 ${p.pipeline || '-'}@${p.version || '-'}  |  ` +
      `输入 ${JSON.stringify(p.inputs || {})}  |  工作目录 ${p.workdir || '-'}`;
  }
  return JSON.stringify(p);
};

export function RunsPage({ params }: { params: URLSearchParams }) {
  const [runs, setRuns] = useState<any[]>([]);
  const [runId, setRunId] = useState(params.get('id') || '');
  const [run, setRun] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [keyword, setKeyword] = useState('');
  const [level, setLevel] = useState('all');
  const [follow, setFollow] = useState(true);
  const [stepFilter, setStepFilter] = useState('');
  const [tab, setTab] = useState<'logs' | 'evidence' | 'steps'>('logs');
  const [outputOpen, setOutputOpen] = useState(false);
  const [wrap, setWrap] = useState(false);
  const [diffPair, setDiffPair] = useState<any>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [confirmDel, setConfirmDel] = useState<any>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  const loadRuns = () => api.get('/api/runs').then(setRuns).catch((e: any) => toast(e.message, 'error'));
  useEffect(() => { loadRuns(); }, []);

  useEffect(() => {
    if (!runId) { setRun(null); return; }
    let alive = true;
    let stopped = false;
    let timer: any;
    const load = async () => {
      try {
        const r: any = await api.get(`/api/runs/${runId}`);
        if (alive) setRun(r);
        // 终态后停止轮询，避免已完成的任务仍每 1.5s 刷 GET /api/runs/{id}
        if (!stopped && (r.finished_at || TERMINAL.has(r.status))) {
          stopped = true;
          clearInterval(timer);
        }
      } catch (e: any) { toast(e.message, 'error'); }
    };
    timer = setInterval(load, 1500);
    load();
    setEvents([]);
    esRef.current?.close();
    const es = new EventSource(sseUrl(`/api/runs/${runId}/events`));
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        setEvents((old) => [...old.slice(-4999), ev]);
      } catch { /* keepalive */ }
    };
    esRef.current = es;
    return () => { alive = false; clearInterval(timer); es.close(); esRef.current = null; };
  }, [runId]);

  useEffect(() => {
    if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [events, follow]);

  const filtered = useMemo(() => events.filter((ev) => {
    if (stepFilter && ev.step !== stepFilter && ev.type !== 'run.started' && ev.type !== 'run.finished') return false;
    if (level === 'error' && !/fail|error|timeout|cancel|attention/i.test(ev.type)) return false;
    if (level === 'stdout' && !/stdout|transfer/i.test(ev.type)) return false;
    if (keyword && !JSON.stringify(ev).toLowerCase().includes(keyword.toLowerCase())) return false;
    return true;
  }), [events, keyword, level, stepFilter]);

  const steps = run?.steps || [];
  const grouped = useMemo(() => {
    const map: Record<string, any[]> = {};
    steps.forEach((s: any) => { (map[s.step_id] ||= []).push(s); });
    return map;
  }, [steps]);

  const artifacts = run?.artifacts || [];
  const stepArtifacts = stepFilter ? artifacts.filter((a: any) => a.step_run_id?.includes(`-${stepFilter}-`)) : artifacts;

  const control = async (action: string, extra = '') => {
    try { const r: any = await api.post(`/api/runs/${runId}/${action}${extra}`); toast(`${action} → ${r.status || 'ok'}`); loadRuns(); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  const showDiff = async (a: any) => {
    try {
      const raw: any = await api.get(`/api/runs/${runId}/artifacts/${a.id}`);
      const lines = String(raw).split('\n');
      const before = lines.filter((l) => !l.startsWith('+') && !l.startsWith('---')).map((l) => l.replace(/^-/, '')).join('\n');
      const after = lines.filter((l) => !l.startsWith('-') && !l.startsWith('+++')).map((l) => l.replace(/^\+/, '')).join('\n');
      setDiffPair({ before, after, name: a.path });
    } catch (e: any) { toast(e.message, 'error'); }
  };

  const downloadLog = () => downloadFile(`/api/runs/${runId}/events.ndjson`, `${runId}-events.ndjson`).catch((e) => toast(e.message, 'error'));

  return <div className="page">
    <div className="page-head"><div><h1>运行面板</h1><p className="sub">步骤树 + 实时日志流(SSE) + 证据 + 运行控制（DR-60/61/63/64）</p></div>
      <div className="row"><button className="btn" onClick={loadRuns}>刷新</button></div></div>
    <div className="grid runs-layout">
      <Section title={`运行历史（${runs.length}）`} className="run-list">
        {!runs.length ? <Empty text="暂无运行" /> : <table><thead><tr><th>Run</th><th>工作项</th><th>阶段</th><th>状态</th><th></th></tr></thead>
          <tbody>{runs.map((r) => <tr key={r.id} className={runId === r.id ? 'row-active' : ''} onClick={() => setRunId(r.id)}>
            <td className="mono">{r.id}</td><td>{r.pipeline_id}</td><td>{r.phase}</td><td><Badge status={r.status} /></td>
            <td><button className="btn tiny danger" onClick={(e) => { e.stopPropagation(); setConfirmDel(r); }}>删除</button></td>
          </tr>)}</tbody></table>}
      </Section>

      <div className="run-detail">
        {!run ? <Section title="步骤树 / 证据 / 控制"><Empty text="从左侧选择一个 run" /></Section> : <>
          <Section title={`${run.id} · ${run.phase}`} actions={<span className="row">
            <Badge status={run.status} />
            <span className="tiny muted">{run.finished_at ? `已完成 ${stamp(run.finished_at)}` : '进行中…'}</span>
          </span>}>
            <div className="row">
              <button className="btn tiny" onClick={() => control('cancel')}>取消</button>
              <button className="btn tiny" onClick={() => control('pause')}>暂停</button>
              <button className="btn tiny" onClick={() => control('resume', '?action=continue')}>继续</button>
              <button className="btn tiny" onClick={() => control('resume', '?action=resume')}>失败处续跑</button>
              <button className="btn tiny danger" onClick={() => control('compensate')}>手动补偿</button>
              <button className="btn tiny" onClick={() => downloadFile(`/api/runs/${runId}/evidence.zip`, `${runId}-evidence.zip`).catch((e) => toast(e.message, 'error'))}>证据 zip</button>
              {['md', 'html', 'csv'].map((f) => <button key={f} className="btn tiny"
                onClick={() => downloadFile(`/api/runs/${runId}/report?fmt=${f}`, `${runId}.${f}`).catch((e) => toast(e.message, 'error'))}>报表 {f.toUpperCase()}</button>)}
            </div>
            {run.error_code && <div className="issue error">{run.error_code}</div>}
          </Section>

          <div className="tabs">
            {(['steps', 'logs', 'evidence'] as const).map((t) => <button key={t}
              className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
              {{ steps: `步骤树 (${steps.length})`, logs: `日志流 (${events.length})`, evidence: `证据 (${artifacts.length})` }[t]}
            </button>)}
          </div>

          {tab === 'steps' && <Section title="步骤树（循环/attempt 展开，点击定位日志与证据）">
            {!steps.length ? <Empty text="暂无步骤" /> : <table><thead><tr><th>步骤</th><th>迭代/attempt</th><th>动作</th><th>状态</th><th>跳过原因</th><th></th></tr></thead>
              <tbody>{Object.entries(grouped).map(([sid, list]: any) => list.map((s: any) => <tr key={s.id}>
                <td>{sid}{list.length > 1 && <span className="badge">loop {s.iteration}</span>}</td>
                <td>{s.iteration}/{s.attempt}</td><td className="mono">{s.action_id}@{s.action_version}</td>
                <td><Badge status={s.status} /></td><td>{s.skip_reason || '-'}</td>
                <td><button className="btn tiny" onClick={() => { setStepFilter(sid); setTab('logs'); }}>定位日志</button></td>
              </tr>))}</tbody></table>}
          </Section>}

          {tab === 'logs' && <Section title="输出查看窗口 · 实时日志流">
            <div className="row filters">
              <input className="input" placeholder="关键字搜索/高亮" value={keyword} onChange={(e) => setKeyword(e.target.value)} />
              <select className="input narrow" value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="all">全部级别</option><option value="stdout">仅 stdout/传输</option><option value="error">仅错误/控制</option>
              </select>
              <select className="input narrow" value={stepFilter} onChange={(e) => setStepFilter(e.target.value)}>
                <option value="">全部步骤</option>{Object.keys(grouped).map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              <label className="check tiny"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />跟随</label>
              <button className="btn tiny" onClick={() => setEvents([])}>清空视图</button>
              <button className="btn tiny" onClick={downloadLog}>下载 NDJSON</button>
              <button className="btn tiny" onClick={() => setOutputOpen(true)}>弹出/全屏查看</button>
            </div>
            <div id="log" className={`log ${wrap ? 'wrap' : ''}`} ref={logRef}>
              {!filtered.length ? <div className="muted">等待事件…（SSE，Last-Event-ID 续传）</div> :
                filtered.map((ev) => <div key={ev.seq}
                  className={`line ${/fail|error/i.test(ev.type) ? 'err' : ''} ${/^step\.(stdout|stderr)$/.test(ev.type) ? 'stdout-line' : ''}`}
                  title="点击展开完整事件 JSON" onClick={() => setExpanded((x) => ({ ...x, [ev.seq]: !x[ev.seq] }))}>
                  <span className="t">{ev.seq} {stamp(ev.ts)}</span> <span className="ev-type">{ev.type}</span>{' '}
                  <span className={/stdout/.test(ev.type) ? 'stdout' : ''}>
                    {String(payloadLine(ev)).replace(ANSI, '').slice(0, 1200)}</span>
                  {expanded[ev.seq] && <pre className="pre tiny">{JSON.stringify(ev, null, 2)}</pre>}
                </div>)}
            </div>
          </Section>}

          {tab === 'evidence' && <Section title="证据浏览（diff / 备份 / 报告 / 传输清单）">
            {!stepArtifacts.length ? <Empty text="暂无证据" /> : stepArtifacts.map((a: any) =>
              <div className="issue" key={a.id}>
                <span className="badge">{a.kind}</span> <span className="mono">{a.path.split('/').pop()}</span>
                <span className="tiny muted">sha {String(a.sha256).slice(0, 12)}… · {a.bytes} B</span>
                <div className="row">
                  <button className="btn tiny" onClick={() => downloadFile(`/api/runs/${runId}/artifacts/${a.id}`, a.path.split('/').pop())}>下载</button>
                  {a.kind === 'diff' && <button className="btn tiny" onClick={() => showDiff(a)}>查看行级 diff</button>}
                </div>
              </div>)}
          </Section>}
        </>}
      </div>
    </div>

    {outputOpen && <Modal title="输出查看窗口（搜索/高亮/自动换行/复制/下载）" wide onClose={() => setOutputOpen(false)}>
      <div className="row"><input className="input" placeholder="搜索日志内容" value={keyword} onChange={(e) => setKeyword(e.target.value)} />
        <label className="check tiny"><input type="checkbox" checked={wrap} onChange={(e) => setWrap(e.target.checked)} />自动换行</label>
        <button className="btn tiny" onClick={() => navigator.clipboard?.writeText(filtered.map((e) => JSON.stringify(e)).join('\n'))}>复制</button>
        <button className="btn tiny" onClick={downloadLog}>下载原始 NDJSON</button></div>
      <pre className="output-window">{filtered.map((e) => JSON.stringify(e)).join('\n').slice(0, 200000) || '暂无输出'}</pre>
    </Modal>}
    {diffPair && <Modal title={`配置 diff · ${diffPair.name}`} wide onClose={() => setDiffPair(null)}>
      <DiffView before={diffPair.before} after={diffPair.after} height={440} />
    </Modal>}
    {confirmDel && <Modal title="删除运行记录" onClose={() => setConfirmDel(null)}>
      <NameConfirm name={confirmDel.id} label="确认删除" onCancel={() => setConfirmDel(null)}
        onConfirm={async () => {
          try {
            await api.del(`/api/runs/${confirmDel.id}`);
            toast(`已删除运行记录 ${confirmDel.id}`);
            if (runId === confirmDel.id) { setRunId(''); setRun(null); setEvents([]); }
            setConfirmDel(null); await loadRuns();
          } catch (e: any) { toast(e.message, 'error'); }
        }} />
    </Modal>}
  </div>;
}
