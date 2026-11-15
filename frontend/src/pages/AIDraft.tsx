/* AI 草案：描述逻辑 → schema/lint 双闸 → 人工接受 → plan 确认（默认关闭）。
   Implements: DR-80, DR-81, DR-82, DR-83 */
import { useState } from 'react';
import { api, toast } from '../api';
import { CodeEditor } from '../Monaco';
import { Modal } from '../ui';

export function AIDraftModal({ onClose, onImported }: { onClose: () => void; onImported?: () => void }) {
  const [description, setDescription] = useState('');
  const [result, setResult] = useState<any>(null);
  const [enabled, setEnabled] = useState(false);

  const generate = async () => {
    try { setResult(await api.post('/api/ai/draft', { description })); }
    catch (e: any) {
      if (e.status === 404) { setEnabled(true); toast('AI 默认关闭；请先在设置页开启（DR-83）', 'warn'); }
      else toast(e.message, 'error');
    }
  };
  const enable = async () => { await api.put('/api/settings', { 'ai.enabled': true }); setEnabled(false); toast('AI 已启用'); };
  const accept = async () => {
    try {
      await api.post('/api/pipelines', { yaml: result.yaml });
      await api.post(`/api/ai/accept?id=${encodeURIComponent(result.id)}`);
      toast('草案已接受为 active，仍需 plan → 确认闸（DR-82）');
      onImported?.(); onClose();
    } catch (e: any) { toast(e.message, 'error'); }
  };

  return <Modal title="AI 草案（四道闸：schema → lint → 人工接受 → plan 确认）" wide onClose={onClose}>
    {enabled && <div className="issue warn"><button className="btn tiny" onClick={enable}>启用本地草案生成器</button> 默认关闭，不产生任何出网请求</div>}
    <textarea className="input" rows={3} placeholder="用中文描述逻辑，例如：解压压缩包，修改 json 配置，上传到远程节点"
      value={description} onChange={(e) => setDescription(e.target.value)} />
    <div className="row end"><button className="btn primary" onClick={generate}>生成草案</button></div>
    {result && <div>
      <p className="tiny muted">状态：{result.status}｜门禁：{result.gates.join(' → ')}｜问题：{result.issues.length}</p>
      {result.issues.map((i: any, k: number) => <div className={`issue ${i.level}`} key={k}>{i.code || ''} {i.message}</div>)}
      <CodeEditor value={result.yaml} height={260} readOnly />
      <div className="row end"><button className="btn primary" disabled={result.status === 'draft_invalid'} onClick={accept}>人工接受并导入</button></div>
    </div>}
  </Modal>;
}
