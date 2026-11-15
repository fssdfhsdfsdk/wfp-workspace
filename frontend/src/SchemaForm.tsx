/* rjsf：由 manifest.params 自动生成参数表单，含 path/node/secret 选择器。
   Implements: DR-14, DR-27, DR-48, DR-50, DR-52 */
import { useEffect, useState } from 'react';
import Form from '@rjsf/core';
import validator from '@rjsf/validator-ajv8';
import { api } from './api';

function useOptions(path: string, label: (x: any) => string) {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => { api.get(path).then(setItems).catch(() => setItems([])); }, [path]);
  return items.map((x) => ({ label: label(x), value: x.id || x.label || String(x) }));
}

function PathWidget(props: any) {
  return <div className="widget-row">
    <input className="input" value={props.value || ''} placeholder="相对 workspace 的路径"
      onChange={(e) => props.onChange(e.target.value)} disabled={props.disabled || props.readonly} />
    <span className="muted tiny">路径基于 workspace，plan 显示绝对目标</span>
  </div>;
}

function DirectoryWidget(props: any) {
  return <input className="input" value={props.value || ''} placeholder="目录（相对 workspace）"
    onChange={(e) => props.onChange(e.target.value)} disabled={props.disabled || props.readonly} />;
}

function NodeWidget(props: any) {
  const opts = useOptions('/api/nodes', (n) => `${n.id} · ${n.host}`);
  return <select className="input" value={props.value || ''} onChange={(e) => props.onChange(e.target.value)}>
    <option value="">（使用当前目标节点）</option>
    {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
  </select>;
}

function SecretWidget(props: any) {
  const opts = useOptions('/api/credentials', (c) => `${c.label} (${c.kind})`);
  return <select className="input" value={props.value || ''} onChange={(e) =>
    props.onChange(e.target.value ? `{{ secret('${e.target.value}') }}` : '')}>
    <option value="">（不注入凭据）</option>
    {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
  </select>;
}

const widgets = { PathWidget, DirectoryWidget, NodeWidget, SecretWidget };

export function buildUiSchema(schema: any): any {
  const ui: any = {};
  const props = schema?.properties || {};
  for (const [key, prop] of Object.entries<any>(props)) {
    const widget = prop['x-widget'];
    if (widget === 'path') ui[key] = { 'ui:widget': 'PathWidget' };
    else if (widget === 'directory') ui[key] = { 'ui:widget': 'DirectoryWidget' };
    else if (widget === 'node') ui[key] = { 'ui:widget': 'NodeWidget' };
    else if (widget === 'secret') ui[key] = { 'ui:widget': 'SecretWidget' };
    else if (prop.type === 'object') ui[key] = buildUiSchema(prop);
    else if (prop.type === 'array' && prop.items?.type === 'object') ui[key] = { items: buildUiSchema(prop.items) };
  }
  return ui;
}

export function SchemaForm(props: {
  schema: any; formData?: any; onChange?: (data: any) => void; onSubmit?: (data: any) => void;
  submitLabel?: string; disabled?: boolean; submitId?: string;
}) {
  const { schema, formData, onChange, onSubmit, submitLabel = '应用', disabled, submitId } = props;
  const safeSchema = schema && Object.keys(schema).length ? schema : { type: 'object', properties: {} };
  return <Form
    schema={safeSchema}
    uiSchema={buildUiSchema(safeSchema)}
    validator={validator}
    widgets={widgets}
    formData={formData || {}}
    disabled={disabled}
    onChange={(e: any) => onChange?.(e.formData)}
    onSubmit={(e: any) => onSubmit?.(e.formData)}
    omitExtraData
    liveOmit
  >
    {onSubmit ? <button id={submitId} className="btn primary" type="submit">{submitLabel}</button> : <span />}
  </Form>;
}
