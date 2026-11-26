"""SQLite 持久层：项目 / 画布节点 / 边 / 模型任务 / 素材。

表结构对应《TapNow平台雏形技术实施报告》第 6、12 节的数据模型，
第一阶段用 SQLite，字段与 PostgreSQL 兼容，后续可无痛迁移。
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT DEFAULT 'draft',
  source_manifest TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS canvas_nodes (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, type TEXT NOT NULL,
  title TEXT, position_x REAL DEFAULT 0, position_y REAL DEFAULT 0,
  inputs TEXT DEFAULT '{}', outputs TEXT DEFAULT '{}',
  status TEXT DEFAULT 'idle', provider TEXT, model TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS canvas_edges (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
  source_node_id TEXT NOT NULL, target_node_id TEXT NOT NULL,
  source_handle TEXT DEFAULT 'output', target_handle TEXT DEFAULT 'input'
);
CREATE TABLE IF NOT EXISTS model_tasks (
  id TEXT PRIMARY KEY, project_id TEXT, node_id TEXT,
  provider TEXT, model TEXT, reported_model TEXT, task_type TEXT, status TEXT DEFAULT 'created',
  provider_task_id TEXT, request_payload TEXT, response_payload TEXT,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
  estimated_cost_cny REAL DEFAULT 0, error TEXT,
  created_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, project_id TEXT, node_id TEXT,
  kind TEXT, filename TEXT, meta TEXT DEFAULT '{}', created_at TEXT,
  folder TEXT DEFAULT '', rights TEXT DEFAULT '',
  subject_id TEXT DEFAULT '', library INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS library_folders (
  path TEXT PRIMARY KEY, created_at TEXT
);
CREATE TABLE IF NOT EXISTS embeddings (
  kind TEXT NOT NULL, ref_id TEXT NOT NULL, text TEXT,
  vector TEXT, dims INTEGER, model TEXT, created_at TEXT,
  PRIMARY KEY (kind, ref_id)
);
CREATE TABLE IF NOT EXISTS qc_corrections (
  id TEXT PRIMARY KEY, qc_node_id TEXT, target_node_id TEXT, project_id TEXT,
  orig_verdict TEXT, human_verdict TEXT, fail_type TEXT DEFAULT '',
  reason TEXT DEFAULT '', context TEXT DEFAULT '{}', created_at TEXT
);
CREATE TABLE IF NOT EXISTS import_tasks (
  id TEXT PRIMARY KEY, project_id TEXT, package_id TEXT, package_version TEXT,
  package_hash TEXT UNIQUE NOT NULL, format TEXT NOT NULL, status TEXT NOT NULL,
  manifest TEXT NOT NULL, audit TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS video_plan_confirmations (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, import_task_id TEXT NOT NULL,
  package_hash TEXT NOT NULL, plan_hash TEXT UNIQUE NOT NULL, scene_ids TEXT NOT NULL,
  generative_media TEXT NOT NULL, paid_generation TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS video_candidates (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, import_task_id TEXT NOT NULL,
  package_hash TEXT NOT NULL, parent_id TEXT, version INTEGER NOT NULL,
  status TEXT NOT NULL, video_asset_id TEXT, manifest TEXT NOT NULL,
  created_at TEXT, finished_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS video_revision_requests (
  id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, scene_id TEXT NOT NULL,
  comment TEXT NOT NULL, status TEXT NOT NULL, new_candidate_id TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS training_reviews (
  id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
  object_version TEXT NOT NULL, content_hash TEXT NOT NULL,
  reviewer TEXT NOT NULL, decision TEXT NOT NULL, comment TEXT DEFAULT '',
  checks TEXT DEFAULT '[]', session_role TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_training_reviews_object
  ON training_reviews(object_type, object_id, created_at);
CREATE TABLE IF NOT EXISTS ingestion_batches (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, summary TEXT NOT NULL,
  results TEXT NOT NULL, folder TEXT DEFAULT '', rights TEXT DEFAULT 'own',
  note TEXT DEFAULT '', uploader_role TEXT DEFAULT '', created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ingestion_reviews (
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, action TEXT NOT NULL,
  comment TEXT DEFAULT '', actor_role TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingestion_reviews_batch
  ON ingestion_reviews(batch_id, created_at);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def insert(table: str, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with _conn() as c:
        c.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))


def update(table: str, id_: str, fields: dict) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), id_])


def get(table: str, id_: str) -> dict | None:
    with _conn() as c:
        r = c.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
        return dict(r) if r else None


def query(table: str, where: str = "1=1", params: tuple = ()) -> list[dict]:
    with _conn() as c:
        rs = c.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()
        return [dict(r) for r in rs]


def jloads(s: str | None) -> dict:
    return json.loads(s) if s else {}
