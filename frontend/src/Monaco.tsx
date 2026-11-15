/* Monaco 离线封装：YAML/JSON/Python/PowerShell 编辑、行级 diff、lint 标记。
   Implements: DR-03, DR-43, DR-45, DR-48, DR-62, DR-63 */
import * as monaco from 'monaco-editor';
import Editor, { DiffEditor, loader } from '@monaco-editor/react';
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution';
import 'monaco-editor/esm/vs/basic-languages/powershell/powershell.contribution';
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution';

(self as any).MonacoEnvironment = { getWorker: () => new editorWorker() };
loader.config({ monaco });

export type Marker = { line: number; column?: number; message: string; severity?: 'error' | 'warning' };

export function setMarkers(editor: any, markers: Marker[]): void {
  const model = editor?.getModel?.();
  if (!model) return;
  monaco.editor.setModelMarkers(model, 'wfp-lint', markers.map((m) => ({
    startLineNumber: Math.max(1, m.line || 1), startColumn: m.column || 1,
    endLineNumber: Math.max(1, m.line || 1), endColumn: 120,
    message: m.message,
    severity: m.severity === 'error' ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
  })));
}

export function CodeEditor(props: {
  value: string;
  language?: string;
  height?: number | string;
  readOnly?: boolean;
  onChange?: (value: string) => void;
  onMount?: (editor: any) => void;
  theme?: string;
}) {
  const { value, language = 'yaml', height = 420, readOnly = false, onChange, onMount } = props;
  return <Editor
    height={height}
    language={language}
    value={value}
    theme="vs-dark"
    onChange={(v) => onChange?.(v || '')}
    onMount={(editor) => onMount?.(editor)}
    options={{
      readOnly, minimap: { enabled: false }, fontSize: 13, lineNumbers: 'on',
      wordWrap: 'on', scrollBeyondLastLine: false, automaticLayout: true,
      tabSize: 2, renderWhitespace: 'selection', fontFamily: 'Consolas, Menlo, monospace',
    }}
  />;
}

export function DiffView(props: { before: string; after: string; height?: number | string; language?: string }) {
  return <DiffEditor
    height={props.height || 360}
    language={props.language || 'yaml'}
    original={props.before}
    modified={props.after}
    theme="vs-dark"
    options={{ readOnly: true, minimap: { enabled: false }, fontSize: 12, renderSideBySide: true }}
  />;
}

export { monaco };
