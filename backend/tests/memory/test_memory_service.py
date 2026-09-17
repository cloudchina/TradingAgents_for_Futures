"""memory_service 骨架测试 - 阶段1 验收最小集。

对应 memory-system-plan.md 第 10.1 节，骨架阶段覆盖：
- migrate 幂等（已目标版本再跑为 no-op）
- write_episode + get_canonical_episode 闭环
- 幂等性：同 symbol+date 不同 run_id 写两次，旧 canonical 自动降级为 0
- parse_failed episode 不在 get_recent_episodes 中
- WAL + busy_timeout PRAGMA 生效
- 部分唯一索引兜底：同 symbol+date 不会出现两条 is_canonical=1
"""
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from models.memory import (
    Episode, EpisodeStatus, Direction, confidence_to_level,
)
from services.memory_service import MemoryService, CURRENT_SCHEMA_VERSION


# ─────────────────────────────────────────────────────────────
# migrate
# ─────────────────────────────────────────────────────────────

def test_migrate_applies_schema_v1(memory_svc: MemoryService):
    """首次 init 后 schema_version 应为 1，核心表存在。"""
    with memory_svc._connect() as conn:
        cur = conn.execute("SELECT MAX(version) FROM schema_version")
        assert cur.fetchone()[0] == CURRENT_SCHEMA_VERSION
        for tbl in (
            "episodes", "semantics", "relation_metrics",
            "notes", "consolidation_runs", "memory_injections",
        ):
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            )
            assert cur.fetchone() is not None, f"表 {tbl} 未创建"


def test_migrate_idempotent(memory_svc: MemoryService):
    """再次 init 不报错、不新增版本记录。"""
    with memory_svc._connect() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version=?",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchone()[0]
    memory_svc.init_if_needed()
    with memory_svc._connect() as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version=?",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchone()[0]
    assert before == 1
    assert after == 1


# ─────────────────────────────────────────────────────────────
# Episode 写入闭环
# ─────────────────────────────────────────────────────────────

def _make_episode(symbol="RB", date="2026-09-18", run_id=None, **kw):
    return Episode(
        symbol=symbol,
        analysis_date=date,
        run_id=run_id or f"task-{symbol}-{date}",
        direction=kw.get("direction", Direction.LONG.value),
        direction_view=kw.get("direction_view", "看多"),
        confidence=kw.get("confidence", 0.65),
        confidence_level=kw.get("confidence_level", "中"),
        contract=kw.get("contract", "RB2601"),
        status=kw.get("status", EpisodeStatus.PENDING.value),
        is_canonical=kw.get("is_canonical", True),
        summary=kw.get("summary", "test"),
        decision=kw.get("decision", {}),
    )


def test_write_and_get_canonical(memory_svc: MemoryService):
    ep = _make_episode()
    ep_id = memory_svc.write_episode(ep)
    assert ep_id > 0

    got = memory_svc.get_canonical_episode("RB", "2026-09-18")
    assert got is not None
    assert got.direction == "long"
    assert got.direction_view == "看多"
    assert got.confidence == pytest.approx(0.65)
    assert got.is_canonical is True


def test_canonical_downgrades_old_run(memory_svc: MemoryService):
    """同 symbol+date 写两次，旧 run 自动降级为 0，新 run 为 1。"""
    memory_svc.write_episode(_make_episode(run_id="run-A"))
    memory_svc.write_episode(_make_episode(run_id="run-B"))

    got = memory_svc.get_canonical_episode("RB", "2026-09-18")
    assert got is not None
    assert got.run_id == "run-B"

    with memory_svc._connect() as conn:
        rows = conn.execute(
            "SELECT run_id, is_canonical FROM episodes "
            "WHERE symbol='RB' AND analysis_date='2026-09-18' ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["run_id"] == "run-A"
    assert rows[0]["is_canonical"] == 0
    assert rows[1]["run_id"] == "run-B"
    assert rows[1]["is_canonical"] == 1


def test_partial_unique_index_blocks_two_canonical(memory_svc: MemoryService):
    """部分唯一索引兜底：直接插两条 is_canonical=1 应触发 IntegrityError。"""
    with memory_svc._connect() as conn:
        conn.execute(
            "INSERT INTO episodes (symbol, analysis_date, run_id, created_at, "
            "is_canonical, status) VALUES ('RB', '2026-09-18', 'A', 'now', 1, 'pending')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO episodes (symbol, analysis_date, run_id, created_at, "
                "is_canonical, status) VALUES ('RB', '2026-09-18', 'B', 'now', 1, 'pending')"
            )


def test_parse_failed_excluded_from_stm(memory_svc: MemoryService):
    """parse_failed 不在 get_recent_episodes（STM 注入）中。"""
    memory_svc.write_episode(_make_episode(
        status=EpisodeStatus.PARSE_FAILED.value, run_id="r1"
    ))
    memory_svc.write_episode(_make_episode(
        status=EpisodeStatus.PENDING.value, run_id="r2", direction="short",
        direction_view="看空",
    ))
    recent = memory_svc.get_recent_episodes("RB", limit=10)
    assert len(recent) == 1
    assert recent[0].run_id == "r2"


def test_unverifiable_outcome_write(memory_svc: MemoryService):
    """【三评 A】update_outcome 写 unverifiable，outcome JSON 字段正确。"""
    ep_id = memory_svc.write_episode(_make_episode())
    memory_svc.update_outcome(
        ep_id, {"unverifiable_reason": "no_csv"}, EpisodeStatus.UNVERIFIABLE.value
    )
    got = memory_svc.get_canonical_episode("RB", "2026-09-18")
    assert got.status == "unverifiable"
    assert got.outcome == {"unverifiable_reason": "no_csv"}


def test_update_outcome_rejects_invalid_status(memory_svc: MemoryService):
    ep_id = memory_svc.write_episode(_make_episode())
    with pytest.raises(ValueError):
        memory_service = MemoryService()  # noqa: F841
        memory_svc.update_outcome(ep_id, {}, "pending")


# ─────────────────────────────────────────────────────────────
# PRAGMA
# ─────────────────────────────────────────────────────────────

def test_wal_mode_enabled(memory_conn):
    """【二评】WAL + busy_timeout 应生效。"""
    mode = memory_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    busy = memory_conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert busy == 5000


# ─────────────────────────────────────────────────────────────
# 三评：字段映射函数
# ─────────────────────────────────────────────────────────────

def test_confidence_to_level_thresholds():
    assert confidence_to_level(0.8) == "高"
    assert confidence_to_level(0.7) == "高"
    assert confidence_to_level(0.69) == "中"
    assert confidence_to_level(0.5) == "中"
    assert confidence_to_level(0.49) == "低"
    assert confidence_to_level(0.0) == "低"


def test_memory_injection_write(memory_svc: MemoryService):
    """【三评 F】埋点表可写。"""
    from models.memory import MemoryInjection
    inj = MemoryInjection(
        run_id="task-1_RB", symbol="RB", segment="stm",
        episode_ids=[1, 2], semantic_ids=[], token_count=120, char_count=80,
    )
    inj_id = memory_svc.write_injection(inj)
    assert inj_id > 0
