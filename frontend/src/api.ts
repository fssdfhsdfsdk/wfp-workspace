/* WFP REST 客户端：统一 token、错误体、任务轮询与下载（DR-01/07/72）。 */
export const TOKEN: string = (window as any).WFP_TOKEN || '';

async function request<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'X-WFP-Token': TOKEN,
    ...(opts.headers as Record<string, string> | undefined),
  };
  if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    let body: any = {};
    try { body = await res.json(); } catch { /* ignore */ }
    const err = new Error(`${body.code ? body.code + ': ' : ''}${body.message || res.statusText}`) as any;
    err.status = res.status;
    err.details = body.details;
    throw err;
  }
  const ct = res.headers.get('content-type') || '';
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T = any>(p: string) => request<T>(p),
  post: <T = any>(p: string, body?: any) => request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T = any>(p: string, body?: any, headers?: Record<string, string>) => request<T>(p, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body), headers }),
  del: <T = any>(p: string) => request<T>(p, { method: 'DELETE' }),
};

export async function pollTask<T = any>(taskId: string, timeoutMs = 120000): Promise<T> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const t: any = await api.get(`/api/tasks/${taskId}`);
    if (t.status === 'done') return t.result as T;
    if (t.status === 'error') throw new Error(t.error?.message || '后台任务失败');
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error('任务超时');
}

export async function downloadFile(path: string, filename?: string): Promise<void> {
  const res = await fetch(path, { headers: { 'X-WFP-Token': TOKEN } });
  if (!res.ok) throw new Error(`下载失败：${res.status}`);
  const blob = await res.blob();
  const cd = res.headers.get('content-disposition') || '';
  const matched = /filename="?([^";]+)"?/.exec(cd);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename || matched?.[1] || path.split('/').pop() || 'download';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export function toast(message: string, kind: 'ok' | 'error' | 'warn' = 'ok'): void {
  window.dispatchEvent(new CustomEvent('wfp-toast', { detail: { message, kind } }));
}

export function sseUrl(path: string): string {
  return `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(TOKEN)}`;
}
