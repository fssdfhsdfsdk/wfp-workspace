-- WFP schema v1（M0 冻结基线）。Implements: DR-08, DR-10, DR-50, DR-68, DR-71
-- 与 architecture-design.md §10 一致；日志与证据不入表（ADR-05）。

CREATE TABLE schema_version(version INTEGER NOT NULL, applied_at TEXT NOT NULL);

CREATE TABLE action(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, tags TEXT,
  latest_version TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  deleted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE action_version(
  action_id TEXT NOT NULL REFERENCES action(id), version TEXT NOT NULL,
  manifest_json TEXT NOT NULL, runtime TEXT NOT NULL,
  impl_path TEXT NOT NULL, impl_sha256 TEXT NOT NULL,
  cap_dry_run TEXT NOT NULL, cap_idempotent TEXT NOT NULL, cap_compensate TEXT NOT NULL,
  needs_admin INTEGER NOT NULL, needs_network INTEGER NOT NULL, side_effects TEXT NOT NULL,
  req_fingerprint TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY(action_id, version));

CREATE TABLE pipeline(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, latest_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE pipeline_version(
  pipeline_id TEXT NOT NULL REFERENCES pipeline(id), version TEXT NOT NULL,
  yaml_path TEXT NOT NULL, yaml_sha256 TEXT NOT NULL, layout_path TEXT,
  inputs_schema_json TEXT, step_count INTEGER NOT NULL,
  deps_json TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(pipeline_id, version));

CREATE TABLE credential(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, scheme TEXT NOT NULL,
  ciphertext BLOB NOT NULL, meta_json TEXT, created_at TEXT NOT NULL);

CREATE TABLE node(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL,
  os_kind TEXT NOT NULL, username TEXT NOT NULL, auth_kind TEXT NOT NULL,
  credential_id TEXT REFERENCES credential(id), description TEXT, tags TEXT,
  default_workdir TEXT, caps_json TEXT, last_probe_at TEXT, created_at TEXT NOT NULL);

CREATE TABLE run(
  id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL, pipeline_version TEXT NOT NULL,
  phase TEXT NOT NULL, status TEXT NOT NULL, trigger TEXT NOT NULL,
  inputs_json TEXT NOT NULL, node_snapshot_json TEXT, action_versions_json TEXT NOT NULL,
  plan_report_path TEXT, confirm_json TEXT, runner_pid INTEGER, heartbeat_at TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, error_code TEXT);

CREATE TABLE step_run(
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id),
  step_id TEXT NOT NULL, iteration INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 1,
  action_id TEXT NOT NULL, action_version TEXT NOT NULL,
  status TEXT NOT NULL, skip_reason TEXT, idempotency_key TEXT,
  resolved_params_sha256 TEXT, exit_code INTEGER, outputs_json TEXT,
  log_offset_start INTEGER, log_offset_end INTEGER, started_at TEXT, finished_at TEXT);
CREATE INDEX idx_step_run_run ON step_run(run_id, step_id);
CREATE INDEX idx_idem ON step_run(idempotency_key, status);

CREATE TABLE journal(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, step_run_id TEXT,
  event TEXT NOT NULL, payload_json TEXT NOT NULL, ts TEXT NOT NULL);
CREATE INDEX idx_journal_run ON journal(run_id, seq);

CREATE TABLE artifact(
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_run_id TEXT,
  kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
  source_target TEXT, created_at TEXT NOT NULL);

CREATE TABLE audit(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT, before_sha256 TEXT, after_sha256 TEXT, detail_json TEXT);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
  BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
  BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;

CREATE TABLE setting(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE singleton_lock(
  name TEXT PRIMARY KEY, holder_pid INTEGER, holder_run TEXT, acquired_at TEXT, heartbeat_at TEXT);
