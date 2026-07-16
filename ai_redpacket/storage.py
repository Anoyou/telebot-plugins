"""AI 红包插件的 SQLite 持久化与事务层。"""

from __future__ import annotations

import json
import math
import random
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 6


class StorageError(RuntimeError):
    """持久化层业务错误。"""


def migrate_database(source: Path | str, target: Path | str) -> bool:
    """Create a consistent SQLite backup at target when migrating legacy storage."""

    source_path = Path(source)
    target_path = Path(target)
    if target_path.exists() or not source_path.is_file():
        return False
    if source_path.resolve() == target_path.resolve():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(f".{target_path.name}.migrating-{uuid.uuid4().hex}")
    try:
        with sqlite3.connect(source_path) as source_db, sqlite3.connect(temporary) as target_db:
            source_db.backup(target_db)
        temporary.replace(target_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


class AIStorage:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS question_bank (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    bank_id TEXT NOT NULL,
                    bank_title TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    answer INTEGER NOT NULL CHECK(answer BETWEEN 0 AND 2),
                    explanation TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    UNIQUE(account_id, bank_id, question)
                );

                CREATE INDEX IF NOT EXISTS idx_question_bank_lookup
                    ON question_bank(account_id, bank_id, id);

                CREATE TABLE IF NOT EXISTS question_source_cache (
                    account_id INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    content TEXT NOT NULL,
                    fetched_at REAL NOT NULL,
                    PRIMARY KEY(account_id, source_url)
                );

                CREATE TABLE IF NOT EXISTS redpacket (
                    id TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    bank_id TEXT NOT NULL,
                    total_amount INTEGER NOT NULL CHECK(total_amount > 0),
                    remaining_amount INTEGER NOT NULL CHECK(remaining_amount >= 0),
                    question_count INTEGER NOT NULL CHECK(question_count > 0),
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    settled_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_redpacket_chat
                    ON redpacket(account_id, chat_id, status, created_at);

                CREATE TABLE IF NOT EXISTS redpacket_question (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    redpacket_id TEXT NOT NULL REFERENCES redpacket(id) ON DELETE CASCADE,
                    question_bank_id INTEGER NOT NULL REFERENCES question_bank(id),
                    reward INTEGER NOT NULL CHECK(reward > 0),
                    reserved_by INTEGER,
                    reserved_until REAL,
                    claimed_by INTEGER,
                    claimed_at REAL,
                    UNIQUE(redpacket_id, question_bank_id)
                );

                CREATE INDEX IF NOT EXISTS idx_redpacket_question_available
                    ON redpacket_question(redpacket_id, claimed_by, reserved_until);

                CREATE TABLE IF NOT EXISTS redpacket_attempt (
                    id TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_display_name TEXT NOT NULL DEFAULT '',
                    chat_id INTEGER NOT NULL,
                    redpacket_id TEXT NOT NULL REFERENCES redpacket(id) ON DELETE CASCADE,
                    question_slot_id INTEGER NOT NULL REFERENCES redpacket_question(id),
                    date TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    success INTEGER NOT NULL DEFAULT 0,
                    reward INTEGER NOT NULL DEFAULT 0,
                    option_order_json TEXT NOT NULL,
                    answer_index INTEGER NOT NULL CHECK(answer_index BETWEEN 0 AND 2),
                    submission_token TEXT NOT NULL,
                    last_submission_key TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(account_id, user_id, redpacket_id, date)
                );

                CREATE INDEX IF NOT EXISTS idx_attempt_daily
                    ON redpacket_attempt(account_id, user_id, date, success, attempts);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_attempt_submission
                    ON redpacket_attempt(last_submission_key)
                    WHERE last_submission_key IS NOT NULL;

                CREATE TABLE IF NOT EXISTS redpacket_limit_reset (
                    account_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    reset_at REAL NOT NULL,
                    PRIMARY KEY(account_id, chat_id, user_id, date)
                );

                CREATE TABLE IF NOT EXISTS redpacket_weekly_report (
                    account_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    published_at REAL NOT NULL,
                    PRIMARY KEY(account_id, chat_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS redpacket_daily_reminder (
                    account_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    packet_date TEXT NOT NULL,
                    published_at REAL NOT NULL,
                    PRIMARY KEY(account_id, chat_id, packet_date)
                );
                """
            )
            redpacket_columns = {
                str(item["name"]) for item in conn.execute("PRAGMA table_info(redpacket)").fetchall()
            }
            if "settled_at" not in redpacket_columns:
                conn.execute("ALTER TABLE redpacket ADD COLUMN settled_at REAL")
            attempt_columns = {
                str(item["name"]) for item in conn.execute("PRAGMA table_info(redpacket_attempt)").fetchall()
            }
            if "user_display_name" not in attempt_columns:
                conn.execute("ALTER TABLE redpacket_attempt ADD COLUMN user_display_name TEXT NOT NULL DEFAULT ''")
            reset_columns = {
                str(item["name"]) for item in conn.execute("PRAGMA table_info(redpacket_limit_reset)").fetchall()
            }
            if "chat_id" not in reset_columns:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute("ALTER TABLE redpacket_limit_reset RENAME TO redpacket_limit_reset_legacy")
                    conn.execute(
                        """
                        CREATE TABLE redpacket_limit_reset (
                            account_id INTEGER NOT NULL,
                            chat_id INTEGER NOT NULL,
                            user_id INTEGER NOT NULL,
                            date TEXT NOT NULL,
                            reset_at REAL NOT NULL,
                            PRIMARY KEY(account_id, chat_id, user_id, date)
                        )
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO redpacket_limit_reset(account_id, chat_id, user_id, date, reset_at)
                        SELECT legacy.account_id, attempt.chat_id, legacy.user_id, legacy.date, MAX(legacy.reset_at)
                        FROM redpacket_limit_reset_legacy AS legacy
                        JOIN redpacket_attempt AS attempt
                          ON attempt.account_id = legacy.account_id
                         AND attempt.user_id = legacy.user_id
                         AND attempt.date = legacy.date
                        GROUP BY legacy.account_id, attempt.chat_id, legacy.user_id, legacy.date
                        """
                    )
                    conn.execute("DROP TABLE redpacket_limit_reset_legacy")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            row = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif int(row["version"]) < SCHEMA_VERSION:
                conn.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,))

    def get_source_cache(self, account_id: int, source_url: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM question_source_cache WHERE account_id = ? AND source_url = ?",
                (account_id, source_url),
            ).fetchone()
        return dict(row) if row else None

    def save_source_cache(self, account_id: int, source_url: str, content: str) -> dict[str, Any]:
        fetched_at = time.time()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO question_source_cache(account_id, source_url, content, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, source_url) DO UPDATE SET
                    content = excluded.content,
                    fetched_at = excluded.fetched_at
                """,
                (account_id, source_url, content, fetched_at),
            )
        return {
            "account_id": account_id,
            "source_url": source_url,
            "content": content,
            "fetched_at": fetched_at,
        }

    def replace_bank(
        self,
        *,
        account_id: int,
        bank_id: str,
        title: str,
        questions: list[dict[str, Any]],
    ) -> int:
        now = time.time()
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE question_bank
                SET active = 0,
                    bank_id = bank_id || ':archive:' || CAST(? AS INTEGER) || ':' || id
                WHERE account_id = ? AND bank_id = ? AND active = 1
                """,
                (now, account_id, bank_id),
            )
            conn.executemany(
                """
                INSERT INTO question_bank(
                    account_id, bank_id, bank_title, question, options_json,
                    answer, explanation, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        account_id,
                        bank_id,
                        title,
                        item["question"],
                        json.dumps(item["options"], ensure_ascii=False),
                        item["answer"],
                        item.get("explanation", ""),
                        item.get("source", ""),
                        now,
                    )
                    for item in questions
                ],
            )
            conn.execute(
                """
                DELETE FROM question_bank
                WHERE active = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM redpacket_question
                      WHERE redpacket_question.question_bank_id = question_bank.id
                  )
                """
            )
        return len(questions)

    def list_banks(self, account_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT bank_id, bank_title, source, COUNT(*) AS question_count,
                       MAX(created_at) AS created_at
                FROM question_bank
                WHERE account_id = ? AND active = 1
                GROUP BY bank_id, bank_title, source
                ORDER BY created_at DESC
                """,
                (account_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_bank_questions(self, account_id: int, bank_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT question, options_json, answer, explanation, source
                FROM question_bank
                WHERE account_id = ? AND bank_id = ? AND active = 1
                ORDER BY id
                """,
                (account_id, bank_id),
            ).fetchall()
        return [
            {
                "question": str(row["question"]),
                "options": json.loads(str(row["options_json"])),
                "answer": int(row["answer"]),
                "explanation": str(row["explanation"] or ""),
                "source": str(row["source"] or ""),
            }
            for row in rows
        ]

    def reset_daily_limit(self, account_id: int, chat_id: int, user_id: int, date: str) -> dict[str, Any]:
        with self.transaction() as conn:
            reset_at = time.time()
            conn.execute(
                """
                INSERT INTO redpacket_limit_reset(account_id, chat_id, user_id, date, reset_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, chat_id, user_id, date) DO UPDATE SET
                    reset_at = excluded.reset_at
                """,
                (account_id, chat_id, user_id, date, reset_at),
            )
        return {
            "account_id": account_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "date": date,
            "reset_at": reset_at,
        }

    def reset_all_daily_limits(self, account_id: int, chat_id: int, date: str) -> dict[str, Any]:
        with self.transaction() as conn:
            reset_at = time.time()
            user_ids = [
                int(row["user_id"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT user_id
                    FROM redpacket_attempt
                    WHERE account_id = ? AND chat_id = ? AND date = ?
                    """,
                    (account_id, chat_id, date),
                ).fetchall()
            ]
            conn.executemany(
                """
                INSERT INTO redpacket_limit_reset(account_id, chat_id, user_id, date, reset_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, chat_id, user_id, date) DO UPDATE SET
                    reset_at = excluded.reset_at
                """,
                [(account_id, chat_id, user_id, date, reset_at) for user_id in user_ids],
            )
        return {
            "account_id": account_id,
            "chat_id": chat_id,
            "date": date,
            "reset_at": reset_at,
            "user_count": len(user_ids),
        }

    def expire_unanswered_attempt(
        self,
        *,
        account_id: int,
        attempt_id: str,
        user_id: int,
        submission_token: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        current = time.time() if now is None else float(now)
        with self.transaction() as conn:
            row = conn.execute(
                """
                SELECT a.id, a.redpacket_id, a.question_slot_id, a.submission_token,
                       a.success, rq.claimed_by, rq.reserved_by, rq.reserved_until
                FROM redpacket_attempt a
                JOIN redpacket_question rq ON rq.id = a.question_slot_id
                WHERE a.id = ? AND a.account_id = ? AND a.user_id = ?
                """,
                (attempt_id, account_id, user_id),
            ).fetchone()
            if row is None:
                return {"expired": True, "already_released": True, "attempt_id": attempt_id}
            if (
                bool(row["success"])
                or row["claimed_by"] is not None
                or int(row["reserved_by"] or 0) != user_id
                or str(row["submission_token"]) != submission_token
                or not row["reserved_until"]
                or float(row["reserved_until"]) > current
            ):
                return None
            deleted = conn.execute(
                """
                DELETE FROM redpacket_attempt
                WHERE id = ? AND account_id = ? AND user_id = ?
                  AND success = 0 AND submission_token = ?
                """,
                (attempt_id, account_id, user_id, submission_token),
            )
            if deleted.rowcount != 1:
                return None
            conn.execute(
                """
                UPDATE redpacket_question
                SET reserved_by = NULL, reserved_until = NULL
                WHERE id = ? AND reserved_by = ? AND claimed_by IS NULL
                """,
                (row["question_slot_id"], user_id),
            )
            return {
                "expired": True,
                "already_released": False,
                "attempt_id": attempt_id,
                "redpacket_id": str(row["redpacket_id"]),
                "question_slot_id": int(row["question_slot_id"]),
            }

    def get_successful_attempt_for_payout(
        self,
        *,
        account_id: int,
        chat_id: int,
        redpacket_id: str,
        attempt_id: str,
        user_id: int,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.*, rq.reward AS slot_reward, rq.claimed_by,
                       q.question, q.options_json AS source_options_json,
                       q.explanation, q.source
                FROM redpacket_attempt a
                JOIN redpacket_question rq ON rq.id = a.question_slot_id
                JOIN question_bank q ON q.id = rq.question_bank_id
                WHERE a.id = ? AND a.account_id = ?
                  AND a.chat_id = ? AND a.redpacket_id = ?
                """,
                (attempt_id, account_id, chat_id, redpacket_id),
            ).fetchone()
        if row is None:
            raise StorageError("奖励记录不存在或已经失效")
        if int(row["user_id"]) != user_id:
            raise StorageError("点点点，不是你的奖励你也点！")
        if not bool(row["success"]) or int(row["claimed_by"] or 0) != user_id or int(row["reward"] or 0) <= 0:
            raise StorageError("这道题还没有可申请补发的奖励")
        result = dict(row)
        result["reward"] = int(row["slot_reward"])
        return result

    def get_user_successful_attempt_for_payout(
        self,
        *,
        account_id: int,
        chat_id: int,
        redpacket_id: str,
        user_id: int,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM redpacket_attempt
                WHERE account_id = ? AND chat_id = ? AND redpacket_id = ?
                  AND user_id = ? AND success = 1 AND reward > 0
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (account_id, chat_id, redpacket_id, user_id),
            ).fetchone()
        if row is None:
            raise StorageError("你在这个红包中没有可申请补发的成功奖励")
        return self.get_successful_attempt_for_payout(
            account_id=account_id,
            chat_id=chat_id,
            redpacket_id=redpacket_id,
            attempt_id=str(row["id"]),
            user_id=user_id,
        )

    def create_redpacket(
        self,
        *,
        redpacket_id: str,
        account_id: int,
        chat_id: int,
        creator_id: int,
        bank_id: str,
        total_amount: int,
        rewards: list[int],
        ttl_seconds: int,
        hard_expires_at: float | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        expires_at = now + ttl_seconds
        if hard_expires_at is not None:
            expires_at = min(expires_at, float(hard_expires_at))
        with self.transaction() as conn:
            questions = conn.execute(
                """
                SELECT id FROM question_bank
                WHERE account_id = ? AND bank_id = ? AND active = 1
                ORDER BY RANDOM() LIMIT ?
                """,
                (account_id, bank_id, len(rewards)),
            ).fetchall()
            if len(questions) < len(rewards):
                raise StorageError(f"题库只有 {len(questions)} 道可用题目，无法创建 {len(rewards)} 题红包")
            conn.execute(
                """
                INSERT INTO redpacket(
                    id, account_id, chat_id, creator_id, bank_id, total_amount,
                    remaining_amount, question_count, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    redpacket_id,
                    account_id,
                    chat_id,
                    creator_id,
                    bank_id,
                    total_amount,
                    total_amount,
                    len(rewards),
                    now,
                    expires_at,
                ),
            )
            conn.executemany(
                "INSERT INTO redpacket_question(redpacket_id, question_bank_id, reward) VALUES (?, ?, ?)",
                [(redpacket_id, row["id"], reward) for row, reward in zip(questions, rewards)],
            )
        return self.get_redpacket(redpacket_id) or {}

    def list_active_redpackets_for_account(self, account_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM redpacket
                WHERE account_id = ? AND status = 'active'
                ORDER BY created_at
                """,
                (account_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def shorten_redpacket_expiration(
        self,
        account_id: int,
        redpacket_id: str,
        expires_at: float,
    ) -> bool:
        with self.transaction() as conn:
            result = conn.execute(
                """
                UPDATE redpacket
                SET expires_at = MIN(expires_at, ?)
                WHERE account_id = ? AND id = ? AND status = 'active'
                """,
                (float(expires_at), account_id, redpacket_id),
            )
        return result.rowcount == 1

    def get_redpacket(self, redpacket_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM redpacket WHERE id = ?", (redpacket_id,)).fetchone()
        return dict(row) if row else None

    def list_redpackets(self, account_id: int, chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM redpacket
                WHERE account_id = ? AND chat_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (account_id, chat_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_active_redpackets(
        self,
        account_id: int,
        chat_id: int,
        limit: int = 10,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        current = time.time() if now is None else float(now)
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE redpacket
                SET status = 'expired'
                WHERE account_id = ? AND chat_id = ?
                  AND status = 'active' AND expires_at <= ?
                """,
                (account_id, chat_id, current),
            )
            rows = conn.execute(
                """
                SELECT p.*,
                       COUNT(CASE WHEN rq.claimed_by IS NOT NULL THEN 1 END) AS claimed_count,
                       COALESCE(SUM(CASE WHEN rq.claimed_by IS NOT NULL THEN rq.reward ELSE 0 END), 0) AS claimed_amount
                FROM redpacket p
                LEFT JOIN redpacket_question rq ON rq.redpacket_id = p.id
                WHERE p.account_id = ? AND p.chat_id = ?
                  AND p.status = 'active' AND p.expires_at > ?
                GROUP BY p.id
                ORDER BY p.created_at DESC
                LIMIT ?
                """,
                (account_id, chat_id, current, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def close_redpacket(self, account_id: int, chat_id: int, redpacket_id: str) -> bool:
        with self.transaction() as conn:
            result = conn.execute(
                """
                UPDATE redpacket SET status = 'closed'
                WHERE id = ? AND account_id = ? AND chat_id = ? AND status = 'active'
                """,
                (redpacket_id, account_id, chat_id),
            )
        return result.rowcount == 1

    def list_unsettled_redpackets(self, account_id: int, now: float | None = None) -> list[dict[str, Any]]:
        current = time.time() if now is None else float(now)
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE redpacket
                SET status = 'expired'
                WHERE account_id = ? AND status = 'active' AND expires_at <= ?
                """,
                (account_id, current),
            )
            rows = conn.execute(
                """
                SELECT * FROM redpacket
                WHERE account_id = ? AND status IN ('finished', 'expired')
                  AND settled_at IS NULL
                ORDER BY created_at
                """,
                (account_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_redpacket_settlement(self, account_id: int, redpacket_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, user_display_name, reward, updated_at
                FROM redpacket_attempt
                WHERE account_id = ? AND redpacket_id = ? AND success = 1
                ORDER BY reward DESC, updated_at, user_id
                """,
                (account_id, redpacket_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_redpacket_settled(self, account_id: int, redpacket_id: str, settled_at: float | None = None) -> bool:
        current = time.time() if settled_at is None else float(settled_at)
        with self.transaction() as conn:
            result = conn.execute(
                """
                UPDATE redpacket SET settled_at = ?
                WHERE account_id = ? AND id = ? AND settled_at IS NULL
                  AND status IN ('finished', 'expired')
                """,
                (current, account_id, redpacket_id),
            )
        return result.rowcount == 1

    def unfinished_redpackets_created_between(
        self,
        account_id: int,
        start_at: float,
        end_at: float,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        current = time.time() if now is None else float(now)
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE redpacket
                SET status = 'expired'
                WHERE account_id = ? AND status = 'active' AND expires_at <= ?
                """,
                (account_id, current),
            )
            rows = conn.execute(
                """
                SELECT p.*,
                       COUNT(CASE WHEN rq.claimed_by IS NOT NULL THEN 1 END) AS claimed_count,
                       COALESCE(SUM(CASE WHEN rq.claimed_by IS NOT NULL THEN rq.reward ELSE 0 END), 0) AS claimed_amount
                FROM redpacket p
                LEFT JOIN redpacket_question rq ON rq.redpacket_id = p.id
                WHERE p.account_id = ?
                  AND p.created_at >= ? AND p.created_at < ?
                  AND p.status = 'active' AND p.expires_at > ?
                  AND p.remaining_amount > 0
                GROUP BY p.id
                ORDER BY p.chat_id, p.created_at
                """,
                (account_id, float(start_at), float(end_at), current),
            ).fetchall()
        return [dict(row) for row in rows]

    def daily_reminder_published(self, account_id: int, chat_id: int, packet_date: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM redpacket_daily_reminder
                WHERE account_id = ? AND chat_id = ? AND packet_date = ?
                """,
                (account_id, chat_id, packet_date),
            ).fetchone()
        return row is not None

    def mark_daily_reminder_published(
        self,
        account_id: int,
        chat_id: int,
        packet_date: str,
        published_at: float | None = None,
    ) -> None:
        current = time.time() if published_at is None else float(published_at)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO redpacket_daily_reminder(account_id, chat_id, packet_date, published_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, chat_id, packet_date) DO UPDATE SET
                    published_at = excluded.published_at
                """,
                (account_id, chat_id, packet_date, current),
            )

    def weekly_leaderboard(
        self,
        account_id: int,
        chat_id: int,
        start_at: float,
        end_at: float,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    user_id,
                    COALESCE(MAX(NULLIF(user_display_name, '')), '用户' || user_id) AS user_display_name,
                    COUNT(*) AS success_count,
                    COALESCE(SUM(reward), 0) AS total_reward
                FROM redpacket_attempt
                WHERE account_id = ? AND chat_id = ? AND success = 1
                  AND updated_at >= ? AND updated_at < ?
                GROUP BY user_id
                """,
                (account_id, chat_id, float(start_at), float(end_at)),
            ).fetchall()
        return [dict(row) for row in rows]

    def weekly_report_chat_ids(self, account_id: int, start_at: float, end_at: float) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id FROM redpacket
                WHERE account_id = ? AND created_at >= ? AND created_at < ?
                UNION
                SELECT chat_id FROM redpacket_attempt
                WHERE account_id = ? AND updated_at >= ? AND updated_at < ?
                ORDER BY chat_id
                """,
                (
                    account_id,
                    float(start_at),
                    float(end_at),
                    account_id,
                    float(start_at),
                    float(end_at),
                ),
            ).fetchall()
        return [int(row["chat_id"]) for row in rows]

    def weekly_report_published(self, account_id: int, chat_id: int, week_start: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM redpacket_weekly_report
                WHERE account_id = ? AND chat_id = ? AND week_start = ?
                """,
                (account_id, chat_id, week_start),
            ).fetchone()
        return row is not None

    def mark_weekly_report_published(
        self,
        account_id: int,
        chat_id: int,
        week_start: str,
        published_at: float | None = None,
    ) -> None:
        current = time.time() if published_at is None else float(published_at)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO redpacket_weekly_report(account_id, chat_id, week_start, published_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, chat_id, week_start) DO UPDATE SET
                    published_at = excluded.published_at
                """,
                (account_id, chat_id, week_start, current),
            )

    def reserve_question(
        self,
        *,
        attempt_id: str,
        account_id: int,
        user_id: int,
        chat_id: int,
        redpacket_id: str,
        date: str,
        submission_token: str,
        reservation_seconds: int,
        daily_limit: int = 1,
        retry_count: int = 1,
        user_display_name: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        success_limit = max(1, int(daily_limit))
        max_attempts = 1 + max(0, int(retry_count))
        with self.transaction() as conn:
            packet = conn.execute(
                "SELECT * FROM redpacket WHERE id = ? AND account_id = ? AND chat_id = ?",
                (redpacket_id, account_id, chat_id),
            ).fetchone()
            if packet is None:
                raise StorageError("红包不存在")
            if packet["status"] != "active":
                raise StorageError("红包已经结束")
            if packet["expires_at"] <= now:
                conn.execute("UPDATE redpacket SET status = 'expired' WHERE id = ?", (redpacket_id,))
                raise StorageError("红包已经过期")

            reset = conn.execute(
                "SELECT reset_at FROM redpacket_limit_reset WHERE account_id = ? AND chat_id = ? AND user_id = ? AND date = ?",
                (account_id, chat_id, user_id, date),
            ).fetchone()
            reset_at = float(reset["reset_at"]) if reset else 0.0
            daily = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), 0) AS successes,
                    COALESCE(MAX(CASE WHEN success = 0 AND attempts >= ? THEN 1 ELSE 0 END), 0) AS failed_out
                FROM redpacket_attempt
                WHERE account_id = ? AND user_id = ? AND date = ? AND updated_at >= ?
                """,
                (max_attempts, account_id, user_id, date, reset_at),
            ).fetchone()
            if daily and (int(daily["successes"]) >= success_limit or bool(daily["failed_out"])):
                raise StorageError("你今天的红包挑战已经结束")

            existing = conn.execute(
                """
                SELECT a.*, q.question, q.options_json AS source_options_json,
                       q.explanation, q.source, rq.reward AS slot_reward, rq.claimed_by,
                       rq.reserved_by, rq.reserved_until
                FROM redpacket_attempt a
                JOIN redpacket_question rq ON rq.id = a.question_slot_id
                JOIN question_bank q ON q.id = rq.question_bank_id
                WHERE a.account_id = ? AND a.user_id = ? AND a.redpacket_id = ? AND a.date = ?
                """,
                (account_id, user_id, redpacket_id, date),
            ).fetchone()
            if existing:
                if user_display_name and str(existing["user_display_name"] or "") != user_display_name:
                    conn.execute(
                        "UPDATE redpacket_attempt SET user_display_name = ? WHERE id = ?",
                        (user_display_name, existing["id"]),
                    )
                if existing["claimed_by"] is not None:
                    raise StorageError("这道红包题已经被领取")
                if existing["reserved_by"] != user_id or not existing["reserved_until"] or existing["reserved_until"] <= now:
                    reserved_until = now + reservation_seconds
                    conn.execute(
                        "UPDATE redpacket_question SET reserved_by = ?, reserved_until = ? WHERE id = ? AND claimed_by IS NULL",
                        (user_id, reserved_until, existing["question_slot_id"]),
                    )
                else:
                    reserved_until = float(existing["reserved_until"])
                existing_result = dict(existing)
                existing_result["reserved_until"] = reserved_until
                existing_result["reused"] = True
                if user_display_name:
                    existing_result["user_display_name"] = user_display_name
                return existing_result

            expired_slots = conn.execute(
                """
                SELECT id FROM redpacket_question
                WHERE redpacket_id = ? AND claimed_by IS NULL
                  AND reserved_until IS NOT NULL AND reserved_until <= ?
                """,
                (redpacket_id, now),
            ).fetchall()
            if expired_slots:
                slot_ids = [int(item["id"]) for item in expired_slots]
                placeholders = ",".join("?" for _ in slot_ids)
                conn.execute(
                    f"DELETE FROM redpacket_attempt WHERE attempts = 0 AND question_slot_id IN ({placeholders})",
                    slot_ids,
                )
            conn.execute(
                """
                UPDATE redpacket_question
                SET reserved_by = NULL, reserved_until = NULL
                WHERE redpacket_id = ? AND claimed_by IS NULL
                  AND reserved_until IS NOT NULL AND reserved_until <= ?
                """,
                (redpacket_id, now),
            )
            slot = conn.execute(
                """
                SELECT rq.id, rq.reward, q.question, q.options_json AS source_options_json,
                       q.answer, q.explanation, q.source
                FROM redpacket_question rq
                JOIN question_bank q ON q.id = rq.question_bank_id
                WHERE rq.redpacket_id = ? AND rq.claimed_by IS NULL AND rq.reserved_by IS NULL
                ORDER BY RANDOM() LIMIT 1
                """,
                (redpacket_id,),
            ).fetchone()
            if slot is None:
                raise StorageError("红包暂时没有可领取的题目，请稍后再试")
            option_order = [0, 1, 2]
            random.SystemRandom().shuffle(option_order)
            answer_index = option_order.index(int(slot["answer"]))
            conn.execute(
                "UPDATE redpacket_question SET reserved_by = ?, reserved_until = ? WHERE id = ?",
                (user_id, now + reservation_seconds, slot["id"]),
            )
            reserved_until = now + reservation_seconds
            conn.execute(
                """
                INSERT INTO redpacket_attempt(
                    id, account_id, user_id, user_display_name, chat_id, redpacket_id, question_slot_id,
                    date, attempts, success, reward, option_order_json, answer_index,
                    submission_token, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    account_id,
                    user_id,
                    user_display_name,
                    chat_id,
                    redpacket_id,
                    slot["id"],
                    date,
                    json.dumps(option_order),
                    answer_index,
                    submission_token,
                    now,
                    now,
                ),
            )
            return {
                "id": attempt_id,
                "user_id": user_id,
                "user_display_name": user_display_name,
                "attempts": 0,
                "success": 0,
                "question": slot["question"],
                "source_options_json": slot["source_options_json"],
                "explanation": slot["explanation"],
                "source": slot["source"],
                "reward": slot["reward"],
                "question_slot_id": int(slot["id"]),
                "option_order_json": json.dumps(option_order),
                "answer_index": answer_index,
                "submission_token": submission_token,
                "reserved_until": reserved_until,
                "reused": False,
            }

    def submit_answer(
        self,
        *,
        attempt_id: str,
        account_id: int,
        user_id: int,
        chat_id: int,
        redpacket_id: str,
        option_index: int,
        submission_token: str,
        submission_key: str,
        retry_count: int,
        daily_limit: int = 1,
        reservation_seconds: int = 30,
        answer_cooldown_seconds: float = 0,
    ) -> dict[str, Any]:
        now = time.time()
        max_attempts = 1 + max(0, int(retry_count))
        success_limit = max(1, int(daily_limit))
        with self.transaction() as conn:
            row = conn.execute(
                """
                SELECT a.*, rq.reward AS slot_reward, rq.claimed_by, rq.reserved_by,
                       rq.reserved_until,
                       q.question, q.options_json AS source_options_json,
                       q.explanation, q.source, p.status AS packet_status,
                       p.expires_at AS packet_expires_at
                FROM redpacket_attempt a
                JOIN redpacket_question rq ON rq.id = a.question_slot_id
                JOIN question_bank q ON q.id = rq.question_bank_id
                JOIN redpacket p ON p.id = a.redpacket_id
                WHERE a.id = ? AND a.account_id = ?
                  AND a.chat_id = ? AND a.redpacket_id = ?
                """,
                (attempt_id, account_id, chat_id, redpacket_id),
            ).fetchone()
            if row is None:
                raise StorageError("答题记录不存在")
            if int(row["user_id"]) != user_id:
                raise StorageError("点点点，不是你的题你也点！")
            if row["submission_token"] != submission_token:
                raise StorageError("答题按钮已经失效")
            if row["last_submission_key"] == submission_key:
                return {
                    **dict(row),
                    "reward": int(row["slot_reward"]),
                    "duplicate": True,
                    "correct": bool(row["success"]),
                }
            if row["success"]:
                return {
                    **dict(row),
                    "reward": int(row["slot_reward"]),
                    "duplicate": True,
                    "correct": True,
                }
            if row["packet_status"] != "active" or row["packet_expires_at"] <= now:
                if row["packet_expires_at"] <= now:
                    conn.execute("UPDATE redpacket SET status = 'expired' WHERE id = ? AND status = 'active'", (row["redpacket_id"],))
                conn.execute(
                    """
                    UPDATE redpacket_question
                    SET reserved_by = NULL, reserved_until = NULL
                    WHERE id = ? AND reserved_by = ? AND claimed_by IS NULL
                    """,
                    (row["question_slot_id"], user_id),
                )
                raise StorageError("红包已经结束或过期")
            if not row["reserved_until"] or float(row["reserved_until"]) <= now:
                raise StorageError("题目答题时间已结束")
            cooldown = max(0.0, float(answer_cooldown_seconds))
            elapsed = now - float(row["updated_at"] or 0)
            if int(row["attempts"] or 0) > 0 and cooldown > 0 and elapsed < cooldown:
                remaining_seconds = max(1, math.ceil(cooldown - elapsed))
                raise StorageError(f"点击太快，请 {remaining_seconds} 秒后再试，本次不会消耗答题机会")
            reset = conn.execute(
                "SELECT reset_at FROM redpacket_limit_reset WHERE account_id = ? AND chat_id = ? AND user_id = ? AND date = ?",
                (account_id, chat_id, user_id, row["date"]),
            ).fetchone()
            reset_at = float(reset["reset_at"]) if reset else 0.0
            daily = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), 0) AS successes,
                    COALESCE(MAX(CASE WHEN success = 0 AND attempts >= ? THEN 1 ELSE 0 END), 0) AS failed_out
                FROM redpacket_attempt
                WHERE account_id = ? AND user_id = ? AND date = ? AND id <> ?
                  AND updated_at >= ?
                """,
                (max_attempts, account_id, user_id, row["date"], attempt_id, reset_at),
            ).fetchone()
            if daily and (int(daily["successes"]) >= success_limit or bool(daily["failed_out"])):
                conn.execute(
                    """
                    UPDATE redpacket_question
                    SET reserved_by = NULL, reserved_until = NULL
                    WHERE id = ? AND reserved_by = ? AND claimed_by IS NULL
                    """,
                    (row["question_slot_id"], user_id),
                )
                raise StorageError("你今天的红包挑战已经结束")
            if row["attempts"] >= max_attempts:
                raise StorageError("你今天的答题次数已经用完")
            if row["reserved_by"] != user_id or row["claimed_by"] is not None:
                raise StorageError("这道红包题已经失效")

            attempts = row["attempts"] + 1
            correct = option_index == row["answer_index"]
            if correct:
                claimed = conn.execute(
                    """
                    UPDATE redpacket_question
                    SET claimed_by = ?, claimed_at = ?, reserved_by = NULL, reserved_until = NULL
                    WHERE id = ? AND claimed_by IS NULL AND reserved_by = ?
                    """,
                    (user_id, now, row["question_slot_id"], user_id),
                )
                if claimed.rowcount != 1:
                    raise StorageError("红包已被领取，请重新选择")
                packet = conn.execute(
                    "SELECT remaining_amount FROM redpacket WHERE id = ? AND status = 'active'",
                    (row["redpacket_id"],),
                ).fetchone()
                if packet is None or packet["remaining_amount"] < row["slot_reward"]:
                    raise StorageError("红包余额不足")
                conn.execute(
                    """
                    UPDATE redpacket
                    SET remaining_amount = remaining_amount - ?,
                        status = CASE WHEN remaining_amount - ? = 0 THEN 'finished' ELSE status END
                    WHERE id = ?
                    """,
                    (row["slot_reward"], row["slot_reward"], row["redpacket_id"]),
                )
                conn.execute(
                    """
                    UPDATE redpacket_attempt
                    SET attempts = ?, success = 1, reward = ?, last_submission_key = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (attempts, row["slot_reward"], submission_key, now, attempt_id),
                )
            else:
                finished = attempts >= max_attempts
                next_submission_token = secrets.token_urlsafe(6)
                next_reserved_until = min(
                    float(row["packet_expires_at"]),
                    now + max(1, int(reservation_seconds)),
                )
                conn.execute(
                    """
                    UPDATE redpacket_attempt
                    SET attempts = ?, submission_token = ?, last_submission_key = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (attempts, next_submission_token, submission_key, now, attempt_id),
                )
                if finished:
                    conn.execute(
                        """
                        UPDATE redpacket_question
                        SET reserved_by = NULL, reserved_until = NULL
                        WHERE id = ? AND reserved_by = ? AND claimed_by IS NULL
                        """,
                        (row["question_slot_id"], user_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE redpacket_question SET reserved_until = ?
                        WHERE id = ? AND reserved_by = ? AND claimed_by IS NULL
                        """,
                        (next_reserved_until, row["question_slot_id"], user_id),
                    )
            return {
                **dict(row),
                "reward": int(row["slot_reward"]),
                "attempts": attempts,
                "correct": correct,
                "finished": correct or attempts >= max_attempts,
                "max_attempts": max_attempts,
                "duplicate": False,
                "submission_token": row["submission_token"] if correct else next_submission_token,
                "reserved_until": None if correct or finished else next_reserved_until,
            }
