"""记忆库服务 - SQLite 持久化层（阶段1骨架）。

设计要点（与 memory-system-plan.md 第四章阶段 1 对齐）：
- SQLite 标准库实现，零新增依赖
- WAL 模式 + busy_timeout=5000 + 每线程独立连接（contextmanager 按需开连接）
- 部分唯一索引 idx_episodes_canonical 兜底"同 symbol+date 只有一个 canonical"
- schema_version 表 + migrate() 按版本号增量 apply，避免后期手工改表

字段命名与 models/memory.py 对应；判据字段（direction/confidence）与展示字段
（direction_view/confidence_level）的拆分见【三评】映射约定。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from core.settings import get_memory_dir, get_memory_path
from models.memory import (
    Episode,
    EpisodeStatus,
    Semantic,
    RelationMetric,
    MemoryInjection,
    ConsolidationRun,
    DIRECTION_TO_VIEW,
    confidence_to_level,
)


# ─────────────────────────────────────────────────────────────
# 常量（plan 文档对应常量集中）
# ─────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 1

# 二.1 STM 回看窗口（horizon 是预测步长，不可混用）
STM_WINDOW = 10
FORECAST_HORIZONS = (5, 10)

# 二.4 LTM 候选集上限：先取最近 N 条再打分，避免全表扫描
LTM_CANDIDATE_LIMIT = 200

# 二.4 reliability 三层 fallback 最小样本数
RELIABILITY_MIN_N = 5

# 二.4 token 预算上限（保守上界，1 中文字 ≈ 1~1.5 token）
TOKEN_BUDGET = {"stm": 800, "ltm": 600, "relation": 400}

# 二.2.B 语义记忆降级判据
SEMANTIC_DOWNGRADE_MIN_INVOKED = 5
SEMANTIC_DOWNGRADE_FAIL_RATIO = 0.5

# 二.2.B 语义记忆准入门槛
SEMANTIC_MIN_EVIDENCE = 3
SEMANTIC_MIN_CONFIDENCE = 0.5

# 二.3 关联图谱最小样本门槛
MIN_SAMPLE_60D = 60
MIN_SAMPLE_120D = 150
MIN_CORR_FOR_LEAD = 0.3  # |corr| 阈值

# 阶段3 record_insight 频次限制
RECORD_INSIGHT_MAX_PER_RUN = 2


# ─────────────────────────────────────────────────────────────
# schema 定义（按 schema_version 增量 apply）
# ─────────────────────────────────────────────────────────────

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    direction TEXT,                  -- long|short|neutral 判据
    direction_view TEXT,             -- 看多|看空|中性 展示
    confidence REAL,                 -- 0.0~1.0 数值 判据
    confidence_level TEXT,           -- 高|中|低 展示
    ref_price REAL,                  -- 仅展示，回填禁用
    contract TEXT,                   -- 审计字段，不参与校准判据
    summary TEXT DEFAULT '',
    key_evidence TEXT,               -- JSON array
    decision TEXT,                    -- JSON object
    horizon INTEGER DEFAULT 10,
    status TEXT DEFAULT 'pending',   -- pending|resolved|unverifiable|parse_failed
    outcome TEXT,                     -- JSON 或 NULL（见 EpisodeStatus 注释）
    is_canonical INTEGER DEFAULT 0,
    invoked_count INTEGER DEFAULT 0,
    failed_invocations INTEGER DEFAULT 0,
    injected_tokens INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_episodes_sym_date
    ON episodes(symbol, analysis_date DESC);

-- 【二评】部分唯一索引：同一 symbol+date 只能有一条 is_canonical=1
-- SQLite 支持部分索引，无此索引时"并发写只有一个 canonical"只能靠应用层事务
CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_canonical
    ON episodes(symbol, analysis_date) WHERE is_canonical = 1;

CREATE TABLE IF NOT EXISTS semantics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    claim TEXT NOT NULL,
    category TEXT,
    evidence_count INTEGER DEFAULT 0,
    confidence REAL DEFAULT 0.0,
    stat_confidence REAL DEFAULT 0.0,
    llm_confidence REAL DEFAULT 0.0,
    first_seen TEXT,
    last_seen TEXT,
    sources TEXT,                    -- JSON array of episode_id
    status TEXT DEFAULT 'draft',     -- draft|active|archived
    invoked_count INTEGER DEFAULT 0,
    failed_invocations INTEGER DEFAULT 0,
    auto_activated_at TEXT,
    reviewed_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_semantics_sym_status
    ON semantics(symbol, status);

CREATE TABLE IF NOT EXISTS relation_metrics (
    pair_key TEXT NOT NULL,          -- "-".join(sorted([a,b]))
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    window INTEGER NOT NULL,         -- 60 or 120
    corr REAL,
    lead_symbol TEXT,
    lag_days INTEGER,
    corr_source TEXT DEFAULT 'static_only',
    sample_size INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pair_key, window)
);

CREATE INDEX IF NOT EXISTS idx_relation_pair
    ON relation_metrics(pair_key);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    content TEXT,
    created_at TEXT,
    author TEXT
);

CREATE TABLE IF NOT EXISTS consolidation_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    input_episode_ids TEXT,          -- JSON array
    last_episode_id INTEGER DEFAULT 0,
    output_semantic_ids TEXT,       -- JSON array
    model TEXT,
    token_usage INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    reviewer TEXT
);

-- 【三评 F】记忆注入埋点表
CREATE TABLE IF NOT EXISTS memory_injections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    injected_at TEXT NOT NULL,
    segment TEXT NOT NULL,           -- stm|ltm|relation
    episode_ids TEXT,                -- JSON array
    semantic_ids TEXT,                -- JSON array
    token_count INTEGER DEFAULT 0,
    char_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_injections_run
    ON memory_injections(run_id, symbol);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


# ─────────────────────────────────────────────────────────────
# MemoryService
# ─────────────────────────────────────────────────────────────

class MemoryService:
    """记忆库单例服务。

    线程模型：sqlite3 默认 check_same_thread=True，分析 daemon 线程与 API
    线程不能共用全局连接；通过 `_thread_local` 每线程独立连接。
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path
        self._thread_local = threading.local()
        self._init_lock = threading.Lock()
        # 自动初始化（仅当 db_path 已确定时；测试场景可延后 init）
        if self._db_path is not None:
            self._ensure_dir_and_migrate()

    # ─── 路径与初始化 ───

    def _resolve_db_path(self) -> Path:
        if self._db_path is not None:
            return Path(self._db_path)
        return get_memory_path("memory.db")

    def _ensure_dir_and_migrate(self) -> None:
        path = self._resolve_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._apply_pragmas(conn)
            self._migrate(conn)

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """【二评】WAL + busy_timeout + 每线程独立连接"""
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """按 schema_version 增量 apply。"""
        # 创建 schema_version 表（首次）
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version INTEGER PRIMARY KEY,"
            " applied_at TEXT NOT NULL"
            ")"
        )
        cur = conn.execute("SELECT MAX(version) FROM schema_version")
        current = cur.fetchone()[0]
        current = current or 0

        migrations = {
            1: SCHEMA_V1,
            # 后续版本在这里挂 ALTER/CREATE
        }
        for v in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
            sql = migrations.get(v)
            if not sql:
                logger.warning(f"memory_service: schema v{v} 无迁移脚本，跳过")
                continue
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (v, datetime.now().isoformat()),
            )
            conn.commit()
            logger.info(f"memory_service: schema v{v} 已 apply")

    @contextmanager
    def _connect(self):
        """每线程独立连接；用完关闭，避免跨线程使用。

        sqlite3 Connection 默认 check_same_thread=True，跨线程会抛
        ProgrammingError；这里每次 context 都开新连接是最简单可靠的方案。
        """
        path = self._resolve_db_path()
        conn = sqlite3.connect(path, check_same_thread=True)
        try:
            self._apply_pragmas(conn)
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── 初始化入口（main.py lifespan 调用） ───

    def init_if_needed(self) -> None:
        """main.py 启动时调用；保证目录与 schema 就绪。"""
        self._ensure_dir_and_migrate()

    # ─── Episode 读写 ───

    def write_episode(self, ep: Episode) -> int:
        """写入一条 episode。

        幂等策略：同一 symbol+date 的旧 canonical 自动降为 0（不删除）；
        部分唯一索引 idx_episodes_canonical 兜底并发场景。

        返回 episode.id。
        """
        with self._connect() as conn:
            # 同 symbol+date 的旧 canonical 降级（不删除，保留对比数据）
            conn.execute(
                "UPDATE episodes SET is_canonical=0 "
                "WHERE symbol=? AND analysis_date=? AND is_canonical=1",
                (ep.symbol, ep.analysis_date),
            )
            cur = conn.execute(
                """
                INSERT INTO episodes (
                    symbol, analysis_date, run_id, created_at,
                    direction, direction_view, confidence, confidence_level,
                    ref_price, contract, summary, key_evidence, decision,
                    horizon, status, outcome, is_canonical,
                    invoked_count, failed_invocations, injected_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ep.symbol, ep.analysis_date, ep.run_id, ep.created_at,
                    ep.direction, ep.direction_view, ep.confidence, ep.confidence_level,
                    ep.ref_price, ep.contract, ep.summary,
                    json.dumps(ep.key_evidence, ensure_ascii=False),
                    json.dumps(ep.decision, ensure_ascii=False),
                    ep.horizon, ep.status,
                    json.dumps(ep.outcome, ensure_ascii=False) if ep.outcome else None,
                    1 if ep.is_canonical else 0,
                    ep.invoked_count, ep.failed_invocations, ep.injected_tokens,
                ),
            )
            return cur.lastrowid or 0

    def get_canonical_episode(self, symbol: str, analysis_date: str) -> Optional[Episode]:
        """取某 symbol+date 的 canonical episode（注入 prompt 用此）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM episodes "
                "WHERE symbol=? AND analysis_date=? AND is_canonical=1 "
                "ORDER BY created_at DESC LIMIT 1",
                (symbol, analysis_date),
            ).fetchone()
            return self._row_to_episode(row) if row else None

    def get_recent_episodes(
        self,
        symbol: str,
        limit: int = STM_WINDOW,
        before_date: Optional[str] = None,
    ) -> List[Episode]:
        """STM/LTM 候选集：取近 N 条 canonical episode（不含 parse_failed/unverifiable）。

        【阶段3】before_date：只取早于该日期的 episode。注入发生在写库之前，
        同一天 force_refresh 重跑时若不加此过滤，会把本次之外的同日旧 run 也当成"历史结论"注入。
        """
        sql = (
            "SELECT * FROM episodes "
            "WHERE symbol=? AND is_canonical=1 "
            "  AND status IN ('pending', 'resolved') "
        )
        params: List[Any] = [symbol]
        if before_date:
            sql += "  AND analysis_date < ? "
            params.append(before_date)
        sql += "ORDER BY analysis_date DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_episode(r) for r in rows]

    def get_hit_rates(self, symbol: str, limit: int = 200) -> Dict[str, Any]:
        """【二.4 / 三评 D】reliability 三层 fallback 的数据源。

        返回 {"symbol_rate": float|None, "symbol_n": int,
              "global_rate": float|None, "global_n": int}
        - symbol_rate：该品种最近 limit 条 resolved canonical episode 的命中率，
          样本数 < RELIABILITY_MIN_N 时置 None（调用方回退到全局）
        - global_rate：全库 resolved canonical episode 命中率，样本不足同样置 None
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, outcome FROM episodes "
                "WHERE is_canonical=1 AND status='resolved' "
                "ORDER BY analysis_date DESC LIMIT ?",
                (limit * 5,),
            ).fetchall()

        sym_hit = sym_total = glob_hit = glob_total = 0
        for r in rows:
            hit = self._extract_hit(r["outcome"])
            if hit is None:
                continue
            glob_total += 1
            glob_hit += 1 if hit else 0
            if r["symbol"] == symbol and sym_total < limit:
                sym_total += 1
                sym_hit += 1 if hit else 0

        return {
            "symbol_rate": (sym_hit / sym_total) if sym_total >= RELIABILITY_MIN_N else None,
            "symbol_n": sym_total,
            "global_rate": (glob_hit / glob_total) if glob_total >= RELIABILITY_MIN_N else None,
            "global_n": glob_total,
        }

    @staticmethod
    def _extract_hit(outcome_raw: Optional[str]) -> Optional[bool]:
        """从 outcome JSON 串取 hit；无 outcome 或字段缺失返回 None。"""
        if not outcome_raw:
            return None
        try:
            data = json.loads(outcome_raw)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        hit = data.get("hit")
        return bool(hit) if isinstance(hit, (bool, int)) else None

    def update_outcome(
        self, episode_id: int, outcome: Dict[str, Any], status: str
    ) -> None:
        """【三评 A】回填 outcome；status 必须为 resolved/unverifiable 之一。"""
        if status not in (EpisodeStatus.RESOLVED.value, EpisodeStatus.UNVERIFIABLE.value):
            raise ValueError(f"update_outcome 不接受 status={status}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE episodes SET outcome=?, status=? WHERE id=?",
                (json.dumps(outcome, ensure_ascii=False), status, episode_id),
            )

    def list_pending_episodes(
        self, symbols: Optional[List[str]] = None, limit: int = 1000
    ) -> List[Episode]:
        """【阶段4】取所有待回填的 episode（status=pending 且 canonical），按日期升序。"""
        sql = (
            "SELECT * FROM episodes WHERE status='pending' AND is_canonical=1 "
        )
        params: List[Any] = []
        if symbols:
            placeholders = ",".join("?" * len(symbols))
            sql += f" AND symbol IN ({placeholders}) "
            params.extend(symbols)
        sql += " ORDER BY analysis_date ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_episode(r) for r in rows]

    def list_episodes_by_status(
        self, status: str, symbols: Optional[List[str]] = None, limit: int = 5000
    ) -> List[Episode]:
        """【阶段4】按 status 取 episode（compute_stats 用）。"""
        sql = "SELECT * FROM episodes WHERE status=? "
        params: List[Any] = [status]
        if symbols:
            placeholders = ",".join("?" * len(symbols))
            sql += f" AND symbol IN ({placeholders}) "
            params.extend(symbols)
        sql += " ORDER BY analysis_date DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_episode(r) for r in rows]

    # ─── Semantic 读写（骨架阶段仅基本方法） ───

    def update_semantic_status(
        self,
        semantic_id: int,
        status: str,
        evidence_count: Optional[int] = None,
        confidence: Optional[float] = None,
        last_seen: Optional[str] = None,
    ) -> None:
        """【阶段4】语义记忆状态流转：draft → pending → active / archived。"""
        sets = ["status=?"]
        params: List[Any] = [status]
        if evidence_count is not None:
            sets.append("evidence_count=?")
            params.append(evidence_count)
        if confidence is not None:
            sets.append("confidence=?")
            params.append(confidence)
        if last_seen:
            sets.append("last_seen=?")
            params.append(last_seen)
        params.append(semantic_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE semantics SET {', '.join(sets)} WHERE id=?", params
            )

    def write_semantic(self, sem: Semantic) -> int:
        """写入一条语义记忆。

        阶段3 的 record_insight 落库一律 status='draft'（防幻觉，见二.2.B）；
        阶段4 巩固 Job 与二次校验负责 draft → active 的流转。
        """
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO semantics (
                    symbol, claim, category, evidence_count,
                    confidence, stat_confidence, llm_confidence,
                    first_seen, last_seen, sources, status,
                    invoked_count, failed_invocations,
                    auto_activated_at, reviewed_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sem.symbol, sem.claim, sem.category, sem.evidence_count,
                    sem.confidence, sem.stat_confidence, sem.llm_confidence,
                    sem.first_seen, sem.last_seen,
                    json.dumps(sem.sources, ensure_ascii=False),
                    sem.status, sem.invoked_count, sem.failed_invocations,
                    sem.auto_activated_at, sem.reviewed_by,
                ),
            )
            return cur.lastrowid or 0

    def list_semantics(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Semantic]:
        """按 symbol / status 查语义记忆（阶段5 前端 review 列表用）。"""
        sql = "SELECT * FROM semantics WHERE 1=1 "
        params: List[Any] = []
        if symbol:
            sql += " AND symbol=? "
            params.append(symbol)
        if status:
            sql += " AND status=? "
            params.append(status)
        sql += " ORDER BY last_seen DESC, id DESC "
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_semantic(r) for r in rows]

    def list_active_semantics(self, symbol: str) -> List[Semantic]:
        """注入 prompt 时硬过滤：status=active AND evidence>=3 AND confidence>=0.5"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM semantics "
                "WHERE symbol=? AND status='active' "
                "  AND evidence_count>=? AND confidence>=? "
                "ORDER BY confidence DESC, last_seen DESC",
                (symbol, SEMANTIC_MIN_EVIDENCE, SEMANTIC_MIN_CONFIDENCE),
            ).fetchall()
            return [self._row_to_semantic(r) for r in rows]

    # ─── MemoryInjection 写入（【三评 F】埋点） ───

    def write_injection(self, inj: MemoryInjection) -> int:
        """每次 build_context 写一条；供 token 度量与 invoked_count 累加。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO memory_injections (
                    run_id, symbol, injected_at, segment,
                    episode_ids, semantic_ids, token_count, char_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inj.run_id, inj.symbol, inj.injected_at, inj.segment,
                    json.dumps(inj.episode_ids, ensure_ascii=False),
                    json.dumps(inj.semantic_ids, ensure_ascii=False),
                    inj.token_count, inj.char_count,
                ),
            )
            return cur.lastrowid or 0

    # ─── 导出（阶段1 plan：memory_export.json） ───

    def export_json(self) -> Dict[str, Any]:
        """导出全库快照到 dict，调用方可写文件备份。"""
        with self._connect() as conn:
            eps = [dict(r) for r in conn.execute("SELECT * FROM episodes").fetchall()]
            sems = [dict(r) for r in conn.execute("SELECT * FROM semantics").fetchall()]
            rels = [dict(r) for r in conn.execute("SELECT * FROM relation_metrics").fetchall()]
            injs = [dict(r) for r in conn.execute("SELECT * FROM memory_injections").fetchall()]
        return {
            "exported_at": datetime.now().isoformat(),
            "episodes": eps,
            "semantics": sems,
            "relation_metrics": rels,
            "memory_injections": injs,
        }

    # ─── Row → Pydantic 映射 ───

    @staticmethod
    def _row_to_episode(row: sqlite3.Row) -> Episode:
        d = dict(row)
        d["key_evidence"] = json.loads(d.get("key_evidence") or "[]")
        d["decision"] = json.loads(d.get("decision") or "{}")
        outcome_raw = d.get("outcome")
        d["outcome"] = json.loads(outcome_raw) if outcome_raw else None
        d["is_canonical"] = bool(d.get("is_canonical"))
        # Pydantic 默认拒绝额外字段，这里只取 Episode 字段
        allowed = {
            "id", "symbol", "analysis_date", "run_id", "created_at",
            "direction", "direction_view", "confidence", "confidence_level",
            "ref_price", "contract", "summary", "key_evidence", "decision",
            "horizon", "status", "outcome", "is_canonical",
            "invoked_count", "failed_invocations", "injected_tokens",
        }
        return Episode(**{k: v for k, v in d.items() if k in allowed})

    @staticmethod
    def _row_to_semantic(row: sqlite3.Row) -> Semantic:
        d = dict(row)
        d["sources"] = json.loads(d.get("sources") or "[]")
        allowed = {
            "id", "symbol", "claim", "category", "evidence_count",
            "confidence", "stat_confidence", "llm_confidence",
            "first_seen", "last_seen", "sources", "status",
            "invoked_count", "failed_invocations",
            "auto_activated_at", "reviewed_by",
        }
        return Semantic(**{k: v for k, v in d.items() if k in allowed})


# ─────────────────────────────────────────────────────────────
# 单例
# ─────────────────────────────────────────────────────────────

memory_service = MemoryService()
