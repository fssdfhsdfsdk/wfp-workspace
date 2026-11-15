/* WFP 离线 SPA 外壳：7 页路由、顶栏当前节点/状态、AI 草案入口、快捷键、Toast。
   Implements: DR-01, DR-07, DR-40, DR-51, DR-72, DR-80, DR-83 */
import { useEffect, useState } from 'react';
import { api, toast } from './api';
import { ToastHost } from './ui';
import { ActionsPage } from './pages/Actions';
import { PipelinesPage } from './pages/Pipelines';
import { EditorPage } from './pages/Editor';
import { RunsPage } from './pages/Runs';
import { NodesPage } from './pages/Nodes';
import { HistoryPage } from './pages/History';
import { SettingsPage } from './pages/Settings';
import { AIDraftModal } from './pages/AIDraft';

const ROUTES: [string, string][] = [
  ['actions', '动作库'], ['pipelines', '工作项'], ['editor', '编排'],
  ['runs', '运行面板'], ['nodes', '节点与凭据'], ['history', '历史与报表'], ['settings', '设置'],
];

function useRoute(): { page: string; params: URLSearchParams } {
  const read = () => location.hash.replace(/^#\/?/, '') || 'pipelines';
  const [raw, setRaw] = useState(read);
  useEffect(() => {
    const fn = () => setRaw(read());
    window.addEventListener('hashchange', fn);
    return () => window.removeEventListener('hashchange', fn);
  }, []);
  const [page, query] = raw.split('?');
  return { page: page || 'pipelines', params: new URLSearchParams(query || '') };
}

export default function App() {
  const { page, params } = useRoute();
  const [currentNode, setCurrentNode] = useState('未选择');
  const [status, setStatus] = useState('就绪');
  const [aiOpen, setAiOpen] = useState(false);

  useEffect(() => {
    api.get('/api/context/current-node').then((r: any) => setCurrentNode(r.current_node || '未选择')).catch(() => {});
  }, [page]);

  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key.toLowerCase() === 's') {
        e.preventDefault(); (document.getElementById('wfp-save-btn') as HTMLElement | null)?.click();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        (document.getElementById(e.shiftKey ? 'wfp-apply-btn' : 'wfp-plan-btn') as HTMLElement | null)?.click();
      }
    };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, []);

  useEffect(() => {
    const fn = (e: any) => setStatus(e.detail || '就绪');
    window.addEventListener('wfp-status', fn as any);
    return () => window.removeEventListener('wfp-status', fn as any);
  }, []);

  const render = () => {
    switch (page) {
      case 'actions': return <ActionsPage />;
      case 'pipelines': return <PipelinesPage />;
      case 'editor': return <EditorPage params={params} />;
      case 'runs': return <RunsPage params={params} />;
      case 'nodes': return <NodesPage />;
      case 'history': return <HistoryPage />;
      case 'settings': return <SettingsPage />;
      default: return <div className="page"><p>未知页面：{page}</p></div>;
    }
  };

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="logo">W</span>
        <div><b>WFP</b><small>Workflow Pipeline Platform</small></div></div>
      <nav className="nav">{ROUTES.map(([p, label]) =>
        <a key={p} href={`#/${p}`} className={page === p ? 'active' : ''}>{label}</a>)}</nav>
      <div className="top-right">
        <span className="chip">当前节点：{currentNode}</span>
        <span className="chip status">{status}</span>
        <button className="btn tiny" onClick={() => setAiOpen(true)}>AI 草案</button>
      </div>
    </header>
    <main className="main">{render()}</main>
    <ToastHost />
    {aiOpen && <AIDraftModal onClose={() => setAiOpen(false)} onImported={() => { toast('草案已导入工作项列表'); location.hash = '#/pipelines'; }} />}
  </div>;
}
