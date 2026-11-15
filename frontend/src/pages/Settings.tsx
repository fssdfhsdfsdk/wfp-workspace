/* 设置：workspace/端口/并发/日志级别/保留策略/AI/编辑器偏好，备份恢复与维护入口。
   Implements: DR-05, DR-06, DR-67, DR-68, DR-69, DR-73, DR-78, DR-79, DR-83 */
import { useEffect, useState } from 'react';
import { api, downloadFile, toast, TOKEN } from '../api';
import { Badge, Section } from '../ui';

export function SettingsPage() {
  const [settings, setSettings] = useState<any>({});
  const [config, setConfig] = useState<any>({});
  const [doctor, setDoctor] = useState<any>(null);
  const [verify, setVerify] = useState<any>(null);
  const [draft, setDraft] = useState<any>({ ...settings });

  const load = async () => {
    try {
      const [s, c]: any = await Promise.all([api.get('/api/settings'), api.get('/api/config')]);
      setSettings(s); setConfig(c); setDraft({ ...s });
    } catch (e: any) { toast(e.message, 'error'); }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    try { await api.put('/api/settings', draft); toast('设置已保存并写入审计（DR-67/68）'); await load(); }
    catch (e: any) { toast(e.message, 'error'); }
  };

  const uploadImport = async (file: File) => {
    const fd = new FormData(); fd.append('file', file);
    const res = await fetch('/api/maintenance/import/upload', { method: 'POST', headers: { 'X-WFP-Token': TOKEN }, body: fd });
    const body: any = await res.json().catch(() => ({}));
    if (!res.ok) toast(body.message || '导入失败', 'error'); else { toast('导入完成，请重启服务使配置生效'); await load(); }
  };

  const fields: [string, string, any][] = [
    ['workspace', 'workspace 根', ''],
    ['port', '监听端口（重启生效）', 8765],
    ['max_apply_runs', 'apply 并发上限', 1],
    ['max_plan_runs', 'plan 并发上限', 2],
    ['log_level', '日志级别', 'info'],
    ['retention_days', '保留天数', 30],
    ['retention_max_bytes', '容量上限（字节）', 5 * 1024 ** 3],
    ['editor.font_size', '编辑器字号', 13],
    ['editor.theme', '编辑器主题', 'dark'],
    ['editor.word_wrap', '自动换行', true],
    ['ai.provider', 'AI provider（默认 off）', 'off'],
  ];

  return <div className="page">
    <div className="page-head"><div><h1>设置</h1><p className="sub">配置、保留策略、AI 开关、编辑器偏好、备份恢复与维护（DR-67）</p></div>
      <div className="row"><button className="btn" onClick={load}>刷新</button></div></div>
    <div className="grid cols2 page-grid">
      <Section title="系统设置（变更写审计）">
        <div className="form-grid">
          {fields.map(([key, label, def]) => <label key={key}>{label}
            <input className="input" value={String(draft[key] ?? settings[key] ?? def)}
              onChange={(e) => {
                const raw = e.target.value;
                const val = typeof def === 'number' ? Number(raw) : raw === 'true' ? true : raw === 'false' ? false : raw;
                setDraft({ ...draft, [key]: val });
              }} /></label>)}
          <label>AI 开关<select className="input" value={String(draft['ai.enabled'] ?? false)}
            onChange={(e) => setDraft({ ...draft, 'ai.enabled': e.target.value === 'true' })}>
            <option value="false">关闭（默认）</option><option value="true">启用本地草案</option></select></label>
        </div>
        <div className="row end"><button className="btn primary" onClick={save}>保存设置</button></div>
        <div className="divider" />
        <h3>生效启动配置（config.toml，只读）</h3>
        <div className="tiny mono pre">{JSON.stringify(config, null, 2)}</div>
      </Section>

      <Section title="维护 / 备份恢复">
        <div className="row wrap-row">
          <button className="btn" onClick={async () => { setDoctor(await api.get('/api/maintenance/doctor')); }}>doctor</button>
          <button className="btn" onClick={async () => { setVerify(await api.get('/api/maintenance/verify')); }}>完整性校验</button>
          <button className="btn" onClick={async () => { await api.post('/api/maintenance/cleanup', {}); toast('清理完成'); }}>按策略清理</button>
          <button className="btn" onClick={() => downloadFile('/api/maintenance/export/download', 'wfp-backup.zip').catch((e) => toast(e.message, 'error'))}>导出全库</button>
          <button className="btn" onClick={() => downloadFile('/api/maintenance/diagnostics/download', 'wfp-diagnostics.zip').catch((e) => toast(e.message, 'error'))}>诊断包</button>
          <label className="btn file-btn">导入备份<input type="file" accept=".zip" onChange={(e) => e.target.files?.[0] && uploadImport(e.target.files[0])} /></label>
        </div>
        {doctor && <div><h3>环境自诊断</h3><Badge status={doctor.ok ? 'succeeded' : 'failed'} />
          {doctor.checks?.map((c: any) => <div className={`issue ${c.ok ? '' : 'error'}`} key={c.name}>
            {c.ok ? '✓' : '✗'} {c.name}：{c.detail}{c.suggestion ? ` → ${c.suggestion}` : ''}</div>)}</div>}
        {verify && <div><h3>完整性</h3><Badge status={verify.ok ? 'succeeded' : 'failed'} />
          <div className="tiny mono pre">{JSON.stringify(verify, null, 2)}</div></div>}
      </Section>
    </div>
  </div>;
}
