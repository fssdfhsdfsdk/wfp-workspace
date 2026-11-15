/* 通用 UI 组件：Toast、Modal、状态徽章、二次确认。 */
import { useEffect, useState } from 'react';

export function ToastHost() {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => {
    const fn = (e: any) => {
      const id = Math.random().toString(36).slice(2);
      setItems((x) => [...x, { id, ...e.detail }]);
      setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 4500);
    };
    window.addEventListener('wfp-toast', fn as any);
    return () => window.removeEventListener('wfp-toast', fn as any);
  }, []);
  return <div className="toast-host">{items.map((i) =>
    <div key={i.id} className={`toast ${i.kind}`}>{i.message}</div>)}</div>;
}

export function Modal(props: { title: string; children: any; onClose: () => void; wide?: boolean }) {
  return <div className="modal-mask" onMouseDown={(e) => { if (e.target === e.currentTarget) props.onClose(); }}>
    <div className={`modal ${props.wide ? 'wide' : ''}`}>
      <div className="modal-head"><b>{props.title}</b><button className="btn tiny" onClick={props.onClose}>关闭</button></div>
      <div className="modal-body">{props.children}</div>
    </div>
  </div>;
}

export function Badge({ status }: { status: string }) {
  const cls = /succeed|planned|active|ok|done|enabled/.test(status) ? 'ok'
    : /fail|error|attention|disabled/.test(status) ? 'err'
      : /skip|warn|queued|draft|pending/.test(status) ? 'warn' : '';
  return <span className={`badge ${cls}`}>{status}</span>;
}

export function Section(props: { title: string; actions?: any; children: any; className?: string }) {
  return <div className={`card ${props.className || ''}`}>
    <div className="card-head"><h3>{props.title}</h3><div className="row">{props.actions}</div></div>
    {props.children}
  </div>;
}

export function NameConfirm(props: { name: string; label?: string; onConfirm: () => void; onCancel: () => void }) {
  const [typed, setTyped] = useState('');
  return <div>
    <p>此操作不可撤销。请输入 <code>{props.name}</code> 确认（DR-42）：</p>
    <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={props.name} />
    <div className="row end">
      <button className="btn" onClick={props.onCancel}>取消</button>
      <button className="btn danger" disabled={typed !== props.name} onClick={props.onConfirm}>
        {props.label || '确认删除'}
      </button>
    </div>
  </div>;
}

export function Empty({ text }: { text: string }) {
  return <div className="empty muted">{text}</div>;
}
