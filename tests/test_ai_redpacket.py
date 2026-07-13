from __future__ import annotations

import asyncio
import importlib
import json
import random
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path


def _install_telepilot_stubs() -> None:
    app = sys.modules.setdefault("app", types.ModuleType("app"))
    worker = sys.modules.setdefault("app.worker", types.ModuleType("app.worker"))
    plugins = sys.modules.setdefault("app.worker.plugins", types.ModuleType("app.worker.plugins"))
    base = types.ModuleType("app.worker.plugins.base")
    manifest = types.ModuleType("app.worker.plugins.manifest")

    class Plugin:
        pass

    class PluginContext:
        pass

    class Manifest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def register(cls):
        return cls

    base.Plugin = Plugin
    base.PluginContext = PluginContext
    base.register = register
    manifest.Manifest = Manifest
    sys.modules["app.worker.plugins.base"] = base
    sys.modules["app.worker.plugins.manifest"] = manifest
    app.worker = worker
    worker.plugins = plugins


_install_telepilot_stubs()
plugin_module = importlib.import_module("ai_redpacket.plugin")
manifest_module = importlib.import_module("ai_redpacket.manifest")
storage_module = importlib.import_module("ai_redpacket.storage")


def _questions(count: int = 5, *, start: int = 0) -> list[dict[str, object]]:
    return [
        {
            "question": f"问题 {index}",
            "options": [f"正确 {index}", f"错误甲 {index}", f"错误乙 {index}"],
            "answer": 0,
            "explanation": f"解析 {index}",
            "source": "https://example.com/source",
        }
        for index in range(start, start + count)
    ]


class RewardAllocationTest(unittest.TestCase):
    def test_integer_rewards_preserve_total_and_bounds(self) -> None:
        rewards = plugin_module.allocate_rewards(100, 10, 1, 20, rng=random.Random(7))
        self.assertEqual(sum(rewards), 100)
        self.assertGreaterEqual(min(rewards), 1)
        self.assertLessEqual(max(rewards), 20)
        self.assertGreater(len(set(rewards)), 1)
        self.assertTrue(all(isinstance(value, int) for value in rewards))

    def test_decimal_amount_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须是整数"):
            plugin_module.allocate_rewards(100.5, 10, 1, 20)

    def test_impossible_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "总金额必须在"):
            plugin_module.allocate_rewards(9, 10, 1, 20)

    def test_forced_average_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "平均分配"):
            plugin_module.allocate_rewards(20, 2, 10, 10)


class QuestionGenerationTest(unittest.TestCase):
    def test_generation_and_user_limits_are_editable_in_supported_ranges(self) -> None:
        properties = manifest_module.CONFIG_SCHEMA["properties"]
        self.assertEqual(properties["generation_count"]["default"], 200)
        self.assertEqual(properties["generation_count"]["minimum"], 100)
        self.assertEqual(properties["generation_count"]["maximum"], 500)
        self.assertEqual(properties["generation_concurrency"]["default"], 3)
        self.assertEqual(properties["generation_concurrency"]["minimum"], 1)
        self.assertEqual(properties["generation_concurrency"]["maximum"], 5)
        self.assertEqual(properties["default_total_amount"]["default"], 150000)
        self.assertEqual(properties["default_questions"]["default"], 40)
        self.assertEqual(properties["reward_max"]["default"], 10000)
        self.assertTrue(properties["weekly_auto_publish"]["default"])
        self.assertNotIn("readOnly", properties["daily_limit"])
        self.assertEqual(properties["daily_limit"]["maximum"], 100)
        self.assertNotIn("readOnly", properties["retry_count"])
        self.assertEqual(properties["retry_count"]["minimum"], 0)
        self.assertEqual(properties["retry_count"]["maximum"], 10)
        self.assertNotIn("readOnly", properties["question_bank_id"])
        self.assertNotIn("x-ui-hidden", properties["question_bank_id"])

    def test_config_fields_are_grouped_into_one_and_two_column_sections(self) -> None:
        properties = manifest_module.CONFIG_SCHEMA["properties"]
        expected = {
            "command": ("基础设置", 2),
            "question_bank_id": ("基础设置", 2),
            "question_source_url": ("题库来源", 1),
            "question_bank_status": ("题库来源", 1),
            "telepilot_provider": ("AI 生成", 2),
            "telepilot_model": ("AI 生成", 2),
            "generation_count": ("AI 生成", 2),
            "max_source_chars": ("AI 生成", 2),
            "generation_concurrency": ("AI 生成", 2),
            "question_generation_prompt": ("AI 出题要求", 1),
            "default_questions": ("红包与答题", 2),
            "default_total_amount": ("红包与答题", 2),
            "daily_limit": ("红包与答题", 2),
            "retry_count": ("红包与答题", 2),
            "reward_min": ("红包与答题", 2),
            "reward_max": ("红包与答题", 2),
            "weekly_auto_publish": ("红包与答题", 2),
        }
        for key, (section, columns) in expected.items():
            self.assertEqual(properties[key]["x-ui-section"], section)
            self.assertEqual(properties[key]["x-ui-columns"], columns)
            self.assertIsInstance(properties[key]["x-ui-order"], int)

    def test_html_cleaner_removes_script_and_style(self) -> None:
        text = plugin_module.clean_html_to_text(
            "<html><style>hidden</style><script>alert(1)</script><article><h1>标题</h1><p>正文 内容</p></article></html>"
        )
        self.assertIn("标题", text)
        self.assertIn("正文 内容", text)
        self.assertNotIn("hidden", text)
        self.assertNotIn("alert", text)

    def test_normalize_rejects_invalid_questions_and_randomizes_answer(self) -> None:
        data = {
            "questions": [
                {
                    "question": "有效题",
                    "options": ["正确", "错误一", "错误二"],
                    "answer": 0,
                    "explanation": "解析",
                },
                {"question": "选项重复", "options": ["A", "A", "B"], "answer": 0},
                {"question": "答案越界", "options": ["A", "B", "C"], "answer": 3},
            ]
        }
        result = plugin_module.normalize_questions(data, "https://example.com", 10)
        self.assertEqual(len(result), 1)
        question = result[0]
        self.assertEqual(len(question["options"]), 3)
        self.assertIn(question["answer"], {0, 1, 2})
        self.assertEqual(question["options"][question["answer"]], "正确")

    def test_extract_json_accepts_fenced_response(self) -> None:
        data = plugin_module.extract_json_object('```json\n{"title":"测试","questions":[]}\n```')
        self.assertEqual(data["title"], "测试")


class StorageMigrationTest(unittest.TestCase):
    def test_version_one_database_adds_source_cache_without_losing_version_state(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ai_redpacket.sqlite3"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
                conn.execute("INSERT INTO schema_meta(version) VALUES (1)")

            storage = storage_module.AIStorage(path)
            storage.save_source_cache(1, "https://example.com/source", "缓存正文")

            with storage.connect() as conn:
                version = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()["version"]
                redpacket_columns = {row["name"] for row in conn.execute("PRAGMA table_info(redpacket)")}
                attempt_columns = {row["name"] for row in conn.execute("PRAGMA table_info(redpacket_attempt)")}
            self.assertEqual(version, 4)
            self.assertIn("settled_at", redpacket_columns)
            self.assertIn("user_display_name", attempt_columns)
            self.assertEqual(storage.get_source_cache(1, "https://example.com/source")["content"], "缓存正文")


class StorageFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage = storage_module.AIStorage(Path(self.tempdir.name) / "ai_redpacket.sqlite3")
        self.storage.replace_bank(account_id=1, bank_id="bank", title="测试题库", questions=_questions())

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _create_packet(self, packet_id: str = "packet", count: int = 3) -> None:
        self.storage.create_redpacket(
            redpacket_id=packet_id,
            account_id=1,
            chat_id=-1001,
            creator_id=99,
            bank_id="bank",
            total_amount=count * 10,
            rewards=[10] * count,
            ttl_seconds=3600,
        )

    def _reserve(self, user_id: int, packet_id: str = "packet") -> dict[str, object]:
        return self.storage.reserve_question(
            attempt_id=f"attempt-{user_id}",
            account_id=1,
            user_id=user_id,
            chat_id=-1001,
            redpacket_id=packet_id,
            date="2026-07-13",
            submission_token=f"token-{user_id}",
            reservation_seconds=300,
        )

    def test_wrong_answer_retries_once_then_ends_day(self) -> None:
        self._create_packet()
        attempt = self._reserve(101)
        wrong = (int(attempt["answer_index"]) + 1) % 3
        first = self.storage.submit_answer(
            attempt_id="attempt-101",
            account_id=1,
            user_id=101,
            chat_id=-1001,
            redpacket_id="packet",
            option_index=wrong,
            submission_token="token-101",
            submission_key="submit-1",
            retry_count=1,
        )
        self.assertFalse(first["correct"])
        self.assertFalse(first["finished"])
        second = self.storage.submit_answer(
            attempt_id="attempt-101",
            account_id=1,
            user_id=101,
            chat_id=-1001,
            redpacket_id="packet",
            option_index=wrong,
            submission_token="token-101",
            submission_key="submit-2",
            retry_count=1,
        )
        self.assertFalse(second["correct"])
        self.assertTrue(second["finished"])
        with self.assertRaisesRegex(storage_module.StorageError, "今天的红包挑战已经结束"):
            self.storage.reserve_question(
                attempt_id="attempt-new",
                account_id=1,
                user_id=101,
                chat_id=-1001,
                redpacket_id="packet",
                date="2026-07-13",
                submission_token="new-token",
                reservation_seconds=300,
            )

    def test_success_deducts_once_and_blocks_daily_repeat(self) -> None:
        self._create_packet()
        attempt = self._reserve(102)
        result = self.storage.submit_answer(
            attempt_id="attempt-102",
            account_id=1,
            user_id=102,
            chat_id=-1001,
            redpacket_id="packet",
            option_index=int(attempt["answer_index"]),
            submission_token="token-102",
            submission_key="success-1",
            retry_count=1,
        )
        self.assertTrue(result["correct"])
        self.assertEqual(result["reward"], 10)
        self.assertEqual(self.storage.get_redpacket("packet")["remaining_amount"], 20)

        duplicate = self.storage.submit_answer(
            attempt_id="attempt-102",
            account_id=1,
            user_id=102,
            chat_id=-1001,
            redpacket_id="packet",
            option_index=int(attempt["answer_index"]),
            submission_token="token-102",
            submission_key="success-1",
            retry_count=1,
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.storage.get_redpacket("packet")["remaining_amount"], 20)

        self._create_packet("packet-two", 2)
        with self.assertRaisesRegex(storage_module.StorageError, "今天的红包挑战已经结束"):
            self._reserve(102, "packet-two")

    def test_configurable_daily_limit_allows_two_successes(self) -> None:
        def complete(packet_id: str, attempt_id: str, token: str) -> None:
            self._create_packet(packet_id, 1)
            attempt = self.storage.reserve_question(
                attempt_id=attempt_id,
                account_id=1,
                user_id=150,
                chat_id=-1001,
                redpacket_id=packet_id,
                date="2026-07-13",
                submission_token=token,
                reservation_seconds=300,
                daily_limit=2,
                retry_count=1,
            )
            result = self.storage.submit_answer(
                attempt_id=attempt_id,
                account_id=1,
                user_id=150,
                chat_id=-1001,
                redpacket_id=packet_id,
                option_index=int(attempt["answer_index"]),
                submission_token=token,
                submission_key=f"submit-{attempt_id}",
                retry_count=1,
                daily_limit=2,
            )
            self.assertTrue(result["correct"])

        complete("limit-one", "limit-attempt-one", "limit-token-one")
        complete("limit-two", "limit-attempt-two", "limit-token-two")
        self._create_packet("limit-three", 1)
        with self.assertRaisesRegex(storage_module.StorageError, "今天的红包挑战已经结束"):
            self.storage.reserve_question(
                attempt_id="limit-attempt-three",
                account_id=1,
                user_id=150,
                chat_id=-1001,
                redpacket_id="limit-three",
                date="2026-07-13",
                submission_token="limit-token-three",
                reservation_seconds=300,
                daily_limit=2,
                retry_count=1,
            )

    def test_configurable_retry_count_allows_two_retries(self) -> None:
        self._create_packet("retry-packet", 1)
        attempt = self.storage.reserve_question(
            attempt_id="retry-attempt",
            account_id=1,
            user_id=151,
            chat_id=-1001,
            redpacket_id="retry-packet",
            date="2026-07-13",
            submission_token="retry-token",
            reservation_seconds=300,
            daily_limit=1,
            retry_count=2,
        )
        wrong = (int(attempt["answer_index"]) + 1) % 3
        results = [
            self.storage.submit_answer(
                attempt_id="retry-attempt",
                account_id=1,
                user_id=151,
                chat_id=-1001,
                redpacket_id="retry-packet",
                option_index=wrong,
                submission_token="retry-token",
                submission_key=f"retry-submit-{index}",
                retry_count=2,
                daily_limit=1,
            )
            for index in range(3)
        ]
        self.assertFalse(results[0]["finished"])
        self.assertFalse(results[1]["finished"])
        self.assertTrue(results[2]["finished"])
        self.assertEqual(results[2]["max_attempts"], 3)

    def test_admin_reset_preserves_history_and_allows_new_daily_attempt(self) -> None:
        self._create_packet("reset-one", 1)
        first = self.storage.reserve_question(
            attempt_id="reset-attempt-one",
            account_id=1,
            user_id=152,
            chat_id=-1001,
            redpacket_id="reset-one",
            date="2026-07-14",
            submission_token="reset-token-one",
            reservation_seconds=300,
        )
        self.storage.submit_answer(
            attempt_id="reset-attempt-one",
            account_id=1,
            user_id=152,
            chat_id=-1001,
            redpacket_id="reset-one",
            option_index=int(first["answer_index"]),
            submission_token="reset-token-one",
            submission_key="reset-submit-one",
            retry_count=1,
            daily_limit=1,
        )
        self._create_packet("reset-two", 1)
        with self.assertRaisesRegex(storage_module.StorageError, "今天的红包挑战已经结束"):
            self.storage.reserve_question(
                attempt_id="reset-attempt-two",
                account_id=1,
                user_id=152,
                chat_id=-1001,
                redpacket_id="reset-two",
                date="2026-07-14",
                submission_token="reset-token-two",
                reservation_seconds=300,
            )

        self.storage.reset_daily_limit(1, 152, "2026-07-14")
        second = self.storage.reserve_question(
            attempt_id="reset-attempt-two",
            account_id=1,
            user_id=152,
            chat_id=-1001,
            redpacket_id="reset-two",
            date="2026-07-14",
            submission_token="reset-token-two",
            reservation_seconds=300,
        )

        self.assertEqual(second["id"], "reset-attempt-two")
        with self.storage.connect() as conn:
            history = conn.execute(
                "SELECT success, reward FROM redpacket_attempt WHERE id = ?",
                ("reset-attempt-one",),
            ).fetchone()
        self.assertEqual(history["success"], 1)
        self.assertEqual(history["reward"], 10)
        self.assertEqual(self.storage.get_redpacket("reset-one")["remaining_amount"], 0)

    def test_single_slot_cannot_be_reserved_concurrently(self) -> None:
        self._create_packet(count=1)
        self._reserve(201)
        with self.assertRaisesRegex(storage_module.StorageError, "暂时没有可领取"):
            self._reserve(202)

    def test_finished_packet_settlement_is_sorted_and_marked_once(self) -> None:
        self.storage.create_redpacket(
            redpacket_id="settlement",
            account_id=1,
            chat_id=-1001,
            creator_id=99,
            bank_id="bank",
            total_amount=30,
            rewards=[20, 10],
            ttl_seconds=3600,
        )
        for user_id, name in ((501, "名字特别特别特别长甲"), (502, "乙")):
            attempt = self.storage.reserve_question(
                attempt_id=f"settlement-{user_id}",
                account_id=1,
                user_id=user_id,
                user_display_name=name,
                chat_id=-1001,
                redpacket_id="settlement",
                date="2026-07-13",
                submission_token=f"settlement-token-{user_id}",
                reservation_seconds=300,
            )
            self.storage.submit_answer(
                attempt_id=f"settlement-{user_id}",
                account_id=1,
                user_id=user_id,
                chat_id=-1001,
                redpacket_id="settlement",
                option_index=int(attempt["answer_index"]),
                submission_token=f"settlement-token-{user_id}",
                submission_key=f"settlement-submit-{user_id}",
                retry_count=1,
            )

        packets = self.storage.list_unsettled_redpackets(1)
        self.assertEqual([packet["id"] for packet in packets], ["settlement"])
        rows = self.storage.get_redpacket_settlement(1, "settlement")
        self.assertEqual([row["reward"] for row in rows], [20, 10])
        self.assertEqual({row["user_display_name"] for row in rows}, {"名字特别特别特别长甲", "乙"})
        self.assertTrue(self.storage.mark_redpacket_settled(1, "settlement"))
        self.assertFalse(self.storage.mark_redpacket_settled(1, "settlement"))
        self.assertEqual(self.storage.list_unsettled_redpackets(1), [])

    def test_expired_packet_becomes_available_for_settlement(self) -> None:
        self._create_packet("expired-settlement", 1)
        packet = self.storage.get_redpacket("expired-settlement")
        rows = self.storage.list_unsettled_redpackets(1, now=float(packet["expires_at"]) + 1)
        self.assertEqual(rows[0]["status"], "expired")

    def test_weekly_leaderboard_aggregates_success_count_and_reward(self) -> None:
        def complete(packet_id: str, user_id: int, reward: int, name: str) -> None:
            self.storage.create_redpacket(
                redpacket_id=packet_id,
                account_id=1,
                chat_id=-1001,
                creator_id=99,
                bank_id="bank",
                total_amount=reward,
                rewards=[reward],
                ttl_seconds=3600,
            )
            attempt = self.storage.reserve_question(
                attempt_id=f"weekly-{packet_id}",
                account_id=1,
                user_id=user_id,
                user_display_name=name,
                chat_id=-1001,
                redpacket_id=packet_id,
                date="2026-07-15",
                submission_token=f"weekly-token-{packet_id}",
                reservation_seconds=300,
                daily_limit=3,
            )
            self.storage.submit_answer(
                attempt_id=f"weekly-{packet_id}",
                account_id=1,
                user_id=user_id,
                chat_id=-1001,
                redpacket_id=packet_id,
                option_index=int(attempt["answer_index"]),
                submission_token=f"weekly-token-{packet_id}",
                submission_key=f"weekly-submit-{packet_id}",
                retry_count=1,
                daily_limit=3,
            )

        complete("weekly-one", 601, 30, "甲")
        complete("weekly-two", 601, 10, "甲")
        complete("weekly-three", 602, 20, "乙")
        start = datetime.fromisoformat("2026-07-12T10:00:00+08:00").timestamp()
        end = datetime.fromisoformat("2026-07-19T10:00:00+08:00").timestamp()
        with self.storage.transaction() as conn:
            conn.execute(
                "UPDATE redpacket_attempt SET updated_at = ? WHERE id LIKE 'weekly-%'",
                (start + 3600,),
            )
            conn.execute(
                "UPDATE redpacket SET created_at = ? WHERE id LIKE 'weekly-%'",
                (start + 1800,),
            )
        rows = self.storage.weekly_leaderboard(1, -1001, start, end)
        by_user = {row["user_id"]: row for row in rows}
        self.assertEqual(by_user[601]["success_count"], 2)
        self.assertEqual(by_user[601]["total_reward"], 40)
        self.assertEqual(by_user[602]["success_count"], 1)
        self.assertEqual(by_user[602]["total_reward"], 20)
        self.assertEqual(self.storage.weekly_report_chat_ids(1, start, end), [-1001])

    def test_server_rejects_tampered_token(self) -> None:
        self._create_packet()
        attempt = self._reserve(301)
        with self.assertRaisesRegex(storage_module.StorageError, "按钮已经失效"):
            self.storage.submit_answer(
                attempt_id="attempt-301",
                account_id=1,
                user_id=301,
                chat_id=-1001,
                redpacket_id="packet",
                option_index=int(attempt["answer_index"]),
                submission_token="tampered",
                submission_key="tampered-submit",
                retry_count=1,
            )

    def test_server_rejects_tampered_chat_or_redpacket_identity(self) -> None:
        self._create_packet()
        attempt = self._reserve(320)
        with self.assertRaisesRegex(storage_module.StorageError, "答题记录不存在"):
            self.storage.submit_answer(
                attempt_id="attempt-320",
                account_id=1,
                user_id=320,
                chat_id=-9999,
                redpacket_id="forged-packet",
                option_index=int(attempt["answer_index"]),
                submission_token="token-320",
                submission_key="forged-context",
                retry_count=1,
            )

    def test_other_user_cannot_answer_reserved_question(self) -> None:
        self._create_packet()
        attempt = self._reserve(321)
        remaining_before = self.storage.get_redpacket("packet")["remaining_amount"]

        with self.assertRaisesRegex(storage_module.StorageError, "点点点，不是你的题你也点"):
            self.storage.submit_answer(
                attempt_id="attempt-321",
                account_id=1,
                user_id=999,
                chat_id=-1001,
                redpacket_id="packet",
                option_index=int(attempt["answer_index"]),
                submission_token="token-321",
                submission_key="other-user-submit",
                retry_count=1,
            )

        with self.storage.connect() as conn:
            saved = conn.execute("SELECT attempts, success FROM redpacket_attempt WHERE id = ?", ("attempt-321",)).fetchone()
        self.assertEqual(saved["attempts"], 0)
        self.assertEqual(saved["success"], 0)
        self.assertEqual(self.storage.get_redpacket("packet")["remaining_amount"], remaining_before)

    def test_bank_refresh_preserves_questions_used_by_active_packet(self) -> None:
        self._create_packet()
        attempt = self._reserve(350)
        self.storage.replace_bank(account_id=1, bank_id="bank", title="新版题库", questions=_questions())
        banks = self.storage.list_banks(1)
        self.assertEqual(len(banks), 1)
        self.assertEqual(banks[0]["bank_title"], "新版题库")
        result = self.storage.submit_answer(
            attempt_id="attempt-350",
            account_id=1,
            user_id=350,
            chat_id=-1001,
            redpacket_id="packet",
            option_index=int(attempt["answer_index"]),
            submission_token="token-350",
            submission_key="refresh-safe-submit",
            retry_count=1,
        )
        self.assertTrue(result["correct"])

    def test_closed_packet_rejects_reserved_answer(self) -> None:
        self._create_packet()
        attempt = self._reserve(360)
        self.assertTrue(self.storage.close_redpacket(1, -1001, "packet"))
        with self.assertRaisesRegex(storage_module.StorageError, "已经结束或过期"):
            self.storage.submit_answer(
                attempt_id="attempt-360",
                account_id=1,
                user_id=360,
                chat_id=-1001,
                redpacket_id="packet",
                option_index=int(attempt["answer_index"]),
                submission_token="token-360",
                submission_key="closed-submit",
                retry_count=1,
            )

    def test_two_pre_reserved_packets_still_allow_only_one_daily_success(self) -> None:
        self._create_packet("packet-one", 2)
        self._create_packet("packet-two", 2)
        first = self._reserve(401, "packet-one")
        second = self.storage.reserve_question(
            attempt_id="attempt-401-two",
            account_id=1,
            user_id=401,
            chat_id=-1001,
            redpacket_id="packet-two",
            date="2026-07-13",
            submission_token="token-401-two",
            reservation_seconds=300,
        )
        self.storage.submit_answer(
            attempt_id="attempt-401",
            account_id=1,
            user_id=401,
            chat_id=-1001,
            redpacket_id="packet-one",
            option_index=int(first["answer_index"]),
            submission_token="token-401",
            submission_key="first-win",
            retry_count=1,
        )
        with self.assertRaisesRegex(storage_module.StorageError, "今天的红包挑战已经结束"):
            self.storage.submit_answer(
                attempt_id="attempt-401-two",
                account_id=1,
                user_id=401,
                chat_id=-1001,
                redpacket_id="packet-two",
                option_index=int(second["answer_index"]),
                submission_token="token-401-two",
                submission_key="second-win",
                retry_count=1,
            )


class PluginActionTest(unittest.TestCase):
    def test_unrelated_command_event_is_ignored(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.AIRedpacketPlugin()

            class Context:
                account_id = 1
                config = {"command": "airp"}
                account_config = {}

            actions = await plugin._handle_command_payload(
                Context(),
                {
                    "source": {"type": "command"},
                    "message": {"chat_id": -1001, "message_id": 1, "text": ",other help"},
                    "sender": {"user_id": 1},
                    "trigger": {"command": "other", "args": ["help"]},
                },
            )
            self.assertEqual(actions, [])

        asyncio.run(run_case())

    def test_decimal_reward_config_is_rejected_without_truncation(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Context:
            config = {"reward_min": 1.5}

        with self.assertRaisesRegex(ValueError, "不得包含小数点"):
            plugin._amount_config(Context(), "reward_min", 1)

    def test_weekly_period_runs_from_sunday_ten_to_next_sunday_ten(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Context:
            config = {"timezone": "Asia/Shanghai"}

        manual_start, manual_end = plugin._weekly_period(
            Context(),
            now=datetime.fromisoformat("2026-07-15T12:00:00+08:00"),
            completed=False,
        )
        self.assertEqual(manual_start.isoformat(), "2026-07-12T10:00:00+08:00")
        self.assertEqual(manual_end.isoformat(), "2026-07-15T12:00:00+08:00")
        completed_start, completed_end = plugin._weekly_period(
            Context(),
            now=datetime.fromisoformat("2026-07-19T10:00:00+08:00"),
            completed=True,
        )
        self.assertEqual(completed_start.isoformat(), "2026-07-12T10:00:00+08:00")
        self.assertEqual(completed_end.isoformat(), "2026-07-19T10:00:00+08:00")

    def test_airp_7_command_returns_collapsed_weekly_message(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")

                class Context:
                    account_id = 1
                    config = {"command": "airp", "timezone": "Asia/Shanghai"}
                    account_config = {"command_prefix": ","}

                actions = await plugin._handle_command_payload(
                    Context(),
                    {
                        "source": {"type": "command"},
                        "message": {"chat_id": -1001, "message_id": 9, "text": ",airp-7"},
                        "sender": {"user_id": 1},
                        "trigger": {"command": "airp-7", "args": []},
                    },
                )
                self.assertIn("AI 红包本周排行榜", actions[0]["text"])

        asyncio.run(run_case())

    def test_failed_message_registers_one_minute_delete_job(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.AIRedpacketPlugin()
            registered = {}
            unregistered = []
            deleted = []

            class Scheduler:
                def register(self, job_id, schedule, callback, **kwargs):
                    registered.update(job_id=job_id, schedule=schedule, callback=callback)

                def unregister(self, job_id):
                    unregistered.append(job_id)

            class Messages:
                async def apply(self, actions, **kwargs):
                    deleted.append((actions, kwargs))

            class Context:
                scheduler = Scheduler()
                messages = Messages()
                log = None

            before = datetime.now().timestamp()
            plugin._schedule_failed_message_delete(Context(), -1001, 88, "attempt-delete")
            fire_at = datetime.fromisoformat(registered["schedule"]["fire_at"]).timestamp()
            self.assertGreaterEqual(fire_at - before, 59)
            self.assertLessEqual(fire_at - before, 61)
            await registered["callback"](types.SimpleNamespace())
            self.assertEqual(
                deleted,
                [
                    (
                        [
                            {
                                "type": "delete_message",
                                "send_via": "interaction_bot",
                                "chat_id": -1001,
                                "message_id": 88,
                            }
                        ],
                        {"entry_key": plugin_module.ENTRY_KEY},
                    )
                ],
            )
            self.assertEqual(unregistered, ["delete_failed_attempt-delete"])

        asyncio.run(run_case())

    def test_settlement_message_is_sorted_collapsed_and_truncates_names(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Storage:
            def get_redpacket_settlement(self, account_id, redpacket_id):
                return [
                    {
                        "user_id": 1,
                        "user_display_name": "一二三四五六七八九十十一十二",
                        "reward": 30,
                        "updated_at": 2.0,
                    },
                    {"user_id": 2, "user_display_name": "小明", "reward": 10, "updated_at": 1.0},
                ]

        class Context:
            account_id = 1

        plugin.storage = Storage()
        messages = plugin._render_redpacket_settlement(
            Context(),
            {
                "id": "packet",
                "status": "finished",
                "total_amount": 40,
                "remaining_amount": 0,
            },
        )
        text = messages[0]
        self.assertIn("运气王：<b>一二三四五六七八九十</b> · 30", text)
        self.assertIn("倒霉蛋：<b>小明</b> · 10", text)
        self.assertIn("<blockquote expandable>", text)
        self.assertLess(text.index("· 30"), text.rindex("· 10"))
        self.assertNotIn("十一十二", text)

    def test_large_settlement_list_is_split_without_losing_entries(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Storage:
            def get_redpacket_settlement(self, account_id, redpacket_id):
                return [
                    {
                        "user_id": index,
                        "user_display_name": f"测试用户名字很长{index}",
                        "reward": 1000 - index,
                        "updated_at": float(index),
                    }
                    for index in range(1, 501)
                ]

        class Context:
            account_id = 1

        plugin.storage = Storage()
        messages = plugin._render_redpacket_settlement(
            Context(),
            {"id": "large", "status": "finished", "total_amount": 500000, "remaining_amount": 0},
        )
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) < 3900 for message in messages))
        self.assertEqual(sum(message.count(" · ") for message in messages), 502)
        self.assertTrue(all("<blockquote expandable>" in message for message in messages))

    def test_weekly_message_ranks_top_five_by_count_and_reward(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Storage:
            def weekly_leaderboard(self, account_id, chat_id, start_at, end_at):
                return [
                    {"user_id": 1, "user_display_name": "答题王", "success_count": 7, "total_reward": 70},
                    {"user_id": 2, "user_display_name": "奖金王", "success_count": 3, "total_reward": 100},
                    {"user_id": 3, "user_display_name": "第三名", "success_count": 2, "total_reward": 20},
                ]

        class Context:
            account_id = 1
            config = {"timezone": "Asia/Shanghai"}

        plugin.storage = Storage()
        text = plugin._render_weekly_leaderboard(
            Context(),
            -1001,
            completed=False,
            now=datetime.fromisoformat("2026-07-15T12:00:00+08:00"),
        )
        self.assertIn("<blockquote expandable>", text)
        count_section, reward_section = text.split("<b>获得奖金 TOP 5</b>")
        self.assertLess(count_section.index("答题王"), count_section.index("奖金王"))
        self.assertLess(reward_section.index("奖金王"), reward_section.index("答题王"))

    def test_startup_registers_settlement_and_sunday_ten_jobs(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.AIRedpacketPlugin()
            jobs = {}
            unregistered = []

            class Scheduler:
                def register(self, job_id, schedule, callback, **kwargs):
                    jobs[job_id] = schedule

                def unregister_all(self):
                    unregistered.append(True)

            class Context:
                account_id = 1
                config = {"command": "airp"}
                account_config = {}
                scheduler = Scheduler()
                log = None

            await plugin.on_startup(Context())
            self.assertEqual(jobs["redpacket_settlement_scan"], {"kind": "interval", "interval_sec": 30})
            self.assertEqual(jobs["weekly_leaderboard"], {"kind": "cron", "cron": "0 10 * * 0"})
            self.assertIn("airp-7", plugin.commands)
            await plugin.on_shutdown(Context())
            self.assertEqual(unregistered, [True])

        asyncio.run(run_case())

    def test_automatic_weekly_report_is_published_once(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.AIRedpacketPlugin()
            published = set()
            sent = []

            class Storage:
                def weekly_report_chat_ids(self, account_id, start_at, end_at):
                    return [-1001]

                def weekly_report_published(self, account_id, chat_id, week_start):
                    return (chat_id, week_start) in published

                def mark_weekly_report_published(self, account_id, chat_id, week_start):
                    published.add((chat_id, week_start))

                def weekly_leaderboard(self, account_id, chat_id, start_at, end_at):
                    return [
                        {"user_id": 1, "user_display_name": "周冠军", "success_count": 5, "total_reward": 88}
                    ]

            class Messages:
                saved = {}

                async def send(self, **kwargs):
                    sent.append(kwargs)
                    self.saved[kwargs["save_message_id_key"]] = 123

                async def read_saved_message_id(self, key):
                    return self.saved.get(key)

            class Context:
                account_id = 1
                config = {"timezone": "Asia/Shanghai", "weekly_auto_publish": True}
                messages = Messages()
                log = None

            plugin.storage = Storage()
            job = types.SimpleNamespace(fired_at=datetime.fromisoformat("2026-07-19T10:00:00+08:00"))
            await plugin._run_weekly_leaderboard(Context(), job)
            await plugin._run_weekly_leaderboard(Context(), job)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["channel"], "interaction_bot")
            self.assertIn("2026-07-12 10:00", sent[0]["text"])
            self.assertIn("2026-07-19 10:00", sent[0]["text"])

        asyncio.run(run_case())

    def test_failed_automatic_weekly_report_schedules_five_minute_retry(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.AIRedpacketPlugin()
            jobs = {}

            class Storage:
                def weekly_report_chat_ids(self, account_id, start_at, end_at):
                    return [-1001]

                def weekly_report_published(self, account_id, chat_id, week_start):
                    return False

                def weekly_leaderboard(self, account_id, chat_id, start_at, end_at):
                    return [
                        {"user_id": 1, "user_display_name": "周冠军", "success_count": 5, "total_reward": 88}
                    ]

            class Messages:
                async def read_saved_message_id(self, key):
                    return None

                async def send(self, **kwargs):
                    return None

            class Scheduler:
                def register(self, job_id, schedule, callback, **kwargs):
                    jobs[job_id] = schedule

            class Context:
                account_id = 1
                config = {"timezone": "Asia/Shanghai", "weekly_auto_publish": True}
                messages = Messages()
                scheduler = Scheduler()
                log = None

            plugin.storage = Storage()
            await plugin._run_weekly_leaderboard(
                Context(),
                types.SimpleNamespace(fired_at=datetime.fromisoformat("2026-07-19T10:00:00+08:00")),
            )
            self.assertEqual(jobs["weekly_leaderboard_retry"]["kind"], "once")
            retry_at = datetime.fromisoformat(jobs["weekly_leaderboard_retry"]["fire_at"])
            self.assertGreaterEqual(retry_at.timestamp() - datetime.now().timestamp(), 299)

        asyncio.run(run_case())

    def test_bare_command_creates_configured_default_redpacket(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(
                    account_id=1,
                    bank_id="default-bank",
                    title="默认题库",
                    questions=_questions(50),
                )

                class Context:
                    account_id = 1
                    config = {
                        "question_bank_id": "default-bank",
                        "default_total_amount": 150000,
                        "default_questions": 40,
                        "reward_min": 1,
                        "reward_max": 10000,
                    }
                    account_config = {}
                    log = None

                actions = await plugin._handle_admin_command(Context(), -1001, 77, 1, [])
                self.assertEqual(actions[0]["send_via"], "interaction_bot")
                packet = plugin.storage.list_redpackets(1, -1001)[0]
                self.assertEqual(packet["total_amount"], 150000)
                self.assertEqual(packet["question_count"], 40)
                self.assertEqual(packet["bank_id"], "default-bank")

        asyncio.run(run_case())

    def test_admin_reset_command_defaults_to_sender(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")

                class Context:
                    account_id = 1
                    config = {"timezone": "Asia/Shanghai"}
                    account_config = {}
                    log = None

                plugin._today = lambda ctx: "2026-07-14"
                actions = await plugin._handle_admin_command(Context(), -1001, 77, 1, ["reset"])
                self.assertIn("用户 <code>77</code>", actions[0]["text"])
                with plugin.storage.connect() as conn:
                    row = conn.execute(
                        "SELECT reset_at FROM redpacket_limit_reset WHERE account_id = ? AND user_id = ? AND date = ?",
                        (1, 77, "2026-07-14"),
                    ).fetchone()
                self.assertIsNotNone(row)

        asyncio.run(run_case())

    def test_config_action_generates_bank_once_and_create_reuses_it(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin._today = lambda ctx: "2026-07-14"

                class HTTP:
                    def __init__(self):
                        self.calls = 0

                    async def get(self, url):
                        self.calls += 1
                        body = "<article><h1>测试资料</h1><p>" + ("这是用于生成题目的网页正文。" * 30) + "</p></article>"
                        return types.SimpleNamespace(status_code=200, text=body)

                class AI:
                    def __init__(self):
                        self.calls = 0
                        self.kwargs = []

                    async def complete(self, **kwargs):
                        self.calls += 1
                        self.kwargs.append(kwargs)
                        return types.SimpleNamespace(
                            text=json.dumps(
                                {
                                    "title": "测试题库",
                                    "questions": _questions(
                                        plugin_module.GENERATION_BATCH_SIZE,
                                        start=(self.calls - 1) * plugin_module.GENERATION_BATCH_SIZE,
                                    ),
                                },
                                ensure_ascii=False,
                            )
                        )

                ai = AI()

                class Context:
                    account_id = 1
                    config = {
                        "question_source_url": "https://example.com/source",
                        "generation_count": 100,
                        "default_questions": 3,
                        "reward_min": 1,
                        "reward_max": 20,
                    }
                    account_config = {}
                    log = None
                    http = HTTP()

                ctx = Context()
                ctx.ai = ai
                action_result = await plugin.on_config_action(
                    ctx,
                    "generate_question_bank",
                    {"config": dict(ctx.config)},
                )
                self.assertIn("题库生成完成", action_result["message"])
                self.assertEqual(action_result["config_patch"]["question_bank_count"], 100)
                self.assertEqual(ai.calls, 9)
                self.assertEqual(ctx.http.calls, 1)
                self.assertEqual(ai.kwargs[0]["route"], "auto")
                self.assertNotIn("provider_tag", ai.kwargs[0])
                self.assertIsNotNone(plugin.storage.get_source_cache(1, "https://example.com/source"))

                repeated = await plugin.on_config_action(
                    ctx,
                    "generate_question_bank",
                    {"config": dict(ctx.config)},
                )
                self.assertIn("已达到目标 100 道", repeated["message"])
                self.assertEqual(ai.calls, 9)
                self.assertEqual(ctx.http.calls, 1)

                create_actions = await plugin._create_packet(ctx, -1001, 99, 12, ["30", "3"])
                self.assertEqual(ai.calls, 9)
                announcement = create_actions[0]
                self.assertEqual(announcement["send_via"], "interaction_bot")
                self.assertEqual(announcement["reply_markup"]["inline_keyboard"][0][0]["text"], "领取答题红包")

        asyncio.run(run_case())

    def test_large_bank_generation_retries_invalid_json_and_saves_atomically(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")

                class HTTP:
                    async def get(self, url):
                        return types.SimpleNamespace(status_code=200, text="可用于出题的网页正文。" * 5000)

                class AI:
                    def __init__(self):
                        self.calls = 0
                        self.kwargs = []

                    async def complete(self, **kwargs):
                        self.calls += 1
                        self.kwargs.append(kwargs)
                        if self.calls == 1:
                            return types.SimpleNamespace(text='{"title":"损坏结果","questions":[{"question":"缺少结尾"}')
                        start = (self.calls - 2) * plugin_module.GENERATION_BATCH_SIZE
                        return types.SimpleNamespace(
                            text=json.dumps(
                                {
                                    "title": "分批题库",
                                    "questions": _questions(plugin_module.GENERATION_BATCH_SIZE, start=start),
                                },
                                ensure_ascii=False,
                            )
                        )

                logs = []

                async def log(level, message, **detail):
                    logs.append((level, message, detail))

                class Context:
                    account_id = 1
                    config = {
                        "question_source_url": "https://example.com/source",
                        "generation_count": 100,
                    }
                    account_config = {}
                    http = HTTP()

                ctx = Context()
                ctx.ai = AI()
                ctx.log = log
                result = await plugin.on_config_action(
                    ctx,
                    "generate_question_bank",
                    {"config": dict(ctx.config)},
                )

                self.assertEqual(result["config_patch"]["question_bank_count"], 100)
                self.assertEqual(ctx.ai.calls, 10)
                self.assertTrue(all(call["max_tokens"] <= 4096 for call in ctx.ai.kwargs))
                self.assertTrue(all("本批只生成" in call["user"] for call in ctx.ai.kwargs))
                self.assertTrue(any(message == "AI 题库分批结果无效，重试后成功" for _, message, _ in logs))
                progress = [detail for _, message, detail in logs if message == "AI 题库生成进度"]
                self.assertTrue(progress)
                self.assertTrue(all({"批次", "目标题数", "已生成题数"} <= set(detail) for detail in progress))
                self.assertTrue(all(not {"batch", "target", "generated"} & set(detail) for detail in progress))
                banks = plugin.storage.list_banks(1)
                self.assertEqual(banks[0]["question_count"], 100)

        asyncio.run(run_case())

    def test_question_batches_run_with_configured_concurrency(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")

                class HTTP:
                    async def get(self, url):
                        return types.SimpleNamespace(status_code=200, text="并发生成使用的有效网页正文。" * 5000)

                class AI:
                    def __init__(self):
                        self.calls = 0
                        self.active = 0
                        self.max_active = 0

                    async def complete(self, **kwargs):
                        index = self.calls
                        self.calls += 1
                        self.active += 1
                        self.max_active = max(self.max_active, self.active)
                        await asyncio.sleep(0.01)
                        self.active -= 1
                        return types.SimpleNamespace(
                            text=json.dumps(
                                {
                                    "title": "并发题库",
                                    "questions": _questions(
                                        plugin_module.GENERATION_BATCH_SIZE,
                                        start=index * plugin_module.GENERATION_BATCH_SIZE,
                                    ),
                                },
                                ensure_ascii=False,
                            )
                        )

                class Context:
                    account_id = 1
                    config = {
                        "question_source_url": "https://example.com/concurrent",
                        "generation_count": 100,
                        "generation_concurrency": 3,
                    }
                    account_config = {}
                    log = None
                    http = HTTP()

                ctx = Context()
                ctx.ai = AI()
                result = await plugin.on_config_action(
                    ctx,
                    "generate_question_bank",
                    {"config": dict(ctx.config)},
                )
                self.assertEqual(result["config_patch"]["question_bank_count"], 100)
                self.assertEqual(ctx.ai.max_active, 3)
                self.assertEqual(ctx.ai.calls, 9)

        asyncio.run(run_case())

    def test_existing_bank_is_extended_from_cached_source(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                url = "https://example.com/source"
                plugin.storage.replace_bank(
                    account_id=1,
                    bank_id="190b4acf8d15",
                    title="已有题库",
                    questions=_questions(5),
                )
                plugin.storage.save_source_cache(1, url, "已经缓存的有效网页正文。" * 2000)

                class HTTP:
                    async def get(self, url):
                        raise AssertionError("扩充题库时不应重新抓取已缓存的网页")

                class AI:
                    def __init__(self):
                        self.calls = 0

                    async def complete(self, **kwargs):
                        self.calls += 1
                        start = 5 + (self.calls - 1) * plugin_module.GENERATION_BATCH_SIZE
                        return types.SimpleNamespace(
                            text=json.dumps(
                                {
                                    "title": "已有题库",
                                    "questions": _questions(plugin_module.GENERATION_BATCH_SIZE, start=start),
                                },
                                ensure_ascii=False,
                            )
                        )

                class Context:
                    account_id = 1
                    config = {"question_source_url": url, "generation_count": 100}
                    account_config = {}
                    log = None
                    http = HTTP()

                ctx = Context()
                ctx.ai = AI()
                result = await plugin.on_config_action(
                    ctx,
                    "generate_question_bank",
                    {"config": dict(ctx.config)},
                )

                self.assertIn("已从 5 道增加到 100 道", result["message"])
                self.assertEqual(ctx.ai.calls, 8)
                questions = plugin.storage.get_bank_questions(1, "190b4acf8d15")
                self.assertEqual(len(questions), 100)
                self.assertTrue(any(item["question"] == "问题 0" for item in questions))

        asyncio.run(run_case())

    def test_selected_provider_and_model_use_fixed_route(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")

                class HTTP:
                    async def get(self, url):
                        return types.SimpleNamespace(status_code=200, text="有效网页正文。" * 80)

                class AI:
                    def __init__(self):
                        self.kwargs = []

                    async def complete(self, **kwargs):
                        self.kwargs.append(kwargs)
                        return types.SimpleNamespace(
                            text=json.dumps(
                                {
                                    "title": "固定模型题库",
                                    "questions": _questions(
                                        plugin_module.GENERATION_BATCH_SIZE,
                                        start=(len(self.kwargs) - 1) * plugin_module.GENERATION_BATCH_SIZE,
                                    ),
                                },
                                ensure_ascii=False,
                            )
                        )

                ai = AI()

                class Context:
                    account_id = 1
                    config = {
                        "question_source_url": "https://example.com/source",
                        "generation_count": 100,
                        "telepilot_provider": "provider-1",
                        "telepilot_model": "model-1",
                    }
                    account_config = {}
                    log = None
                    http = HTTP()

                ctx = Context()
                ctx.ai = ai
                await plugin.on_config_action(ctx, "generate_question_bank", {"config": dict(ctx.config)})
                self.assertEqual(len(ai.kwargs), 9)
                self.assertTrue(all(item["route"] == "fixed" for item in ai.kwargs))
                self.assertTrue(all(item["provider"] == "provider-1" for item in ai.kwargs))
                self.assertTrue(all(item["model"] == "model-1" for item in ai.kwargs))
                self.assertTrue(all("provider_tag" not in item for item in ai.kwargs))

        asyncio.run(run_case())

    def test_create_packet_prefers_configured_default_bank(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin._today = lambda ctx: "2026-07-14"
                plugin.storage.replace_bank(
                    account_id=1,
                    bank_id="default-bank",
                    title="默认题库",
                    questions=_questions(3),
                )
                plugin.storage.replace_bank(
                    account_id=1,
                    bank_id="newer-bank",
                    title="更新题库",
                    questions=_questions(3, start=10),
                )

                class Context:
                    account_id = 1
                    config = {
                        "question_bank_id": "default-bank",
                        "default_questions": 1,
                        "daily_limit": 3,
                        "retry_count": 2,
                        "reward_min": 1,
                        "reward_max": 20,
                        "packet_message_template": "日期 {date} 限制 {daily_limit}/{retry_count}",
                    }
                    account_config = {}
                    log = None

                actions = await plugin._create_packet(Context(), -1001, 99, 1, ["10"])
                self.assertEqual(actions[0]["send_via"], "interaction_bot")
                self.assertEqual(actions[0]["text"], "日期 2026-07-14 限制 3/2")
                packets = plugin.storage.list_redpackets(1, -1001)
                self.assertEqual(packets[0]["bank_id"], "default-bank")

        asyncio.run(run_case())

    def test_limit_placeholders_render_in_question_and_result_templates(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()
        plugin._today = lambda ctx: "2026-07-14"

        class Context:
            config = {
                "daily_limit": 3,
                "retry_count": 2,
                "question_message_template": "题面 {date} {daily_limit}/{retry_count} {question} {options}",
                "success_message_template": "成功 {date} {daily_limit}/{retry_count} {question} {reward}",
                "failed_message_template": "失败 {date} {daily_limit}/{retry_count} {question} {answer}",
            }

        payload = {
            "question": "测试题",
            "source_options_json": json.dumps(["正确", "错误一", "错误二"]),
            "option_order_json": json.dumps([0, 1, 2]),
            "answer_index": 0,
            "reward": 10,
            "explanation": "解析",
            "source": "https://example.com/source",
        }
        self.assertIn("题面 2026-07-14 3/2", plugin._render_question(Context(), payload))
        self.assertIn("成功 2026-07-14 3/2", plugin._render_result(Context(), payload, correct=True))
        self.assertIn("失败 2026-07-14 3/2", plugin._render_result(Context(), payload, correct=False))

    def test_other_user_answer_button_shows_owner_warning_alert(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="rp-owner",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )
                attempt = plugin.storage.reserve_question(
                    attempt_id="owner-attempt",
                    account_id=1,
                    user_id=77,
                    chat_id=-1001,
                    redpacket_id="rp-owner",
                    date="2026-07-14",
                    submission_token="owner-token",
                    reservation_seconds=300,
                )

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    log = None

                actions = await plugin._submit_attempt(
                    Context(),
                    {"message": {"chat_id": -1001, "message_id": 88}},
                    "other-user-callback",
                    -1001,
                    999,
                    "rp-owner",
                    "owner-attempt",
                    int(attempt["answer_index"]),
                    "owner-token",
                )
                self.assertEqual(actions[0]["text"], "点点点，不是你的题你也点！")
                self.assertTrue(actions[0]["show_alert"])

        asyncio.run(run_case())

    def test_success_action_uses_stable_payout_key_and_integer_amount(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="rp1",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )
                attempt = plugin.storage.reserve_question(
                    attempt_id="a1",
                    account_id=1,
                    user_id=77,
                    chat_id=-1001,
                    redpacket_id="rp1",
                    date="2026-07-13",
                    submission_token="tok",
                    reservation_seconds=300,
                )

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    log = None

                payload = {
                    "source": {"type": "callback_query"},
                    "message": {"chat_id": -1001, "message_id": 88},
                    "sender": {"user_id": 77},
                }
                actions = await plugin._submit_attempt(
                    Context(),
                    payload,
                    "callback-1",
                    -1001,
                    77,
                    "rp1",
                    "a1",
                    int(attempt["answer_index"]),
                    "tok",
                )
                payout = next(action for action in actions if action["type"] == "payout")
                self.assertEqual(payout["amount"], 10)
                self.assertIsInstance(payout["amount"], int)
                self.assertEqual(payout["payout_key"], "ai_redpacket:1:rp1:1:77")
                self.assertEqual(payout["reply_to_user_id"], 77)

                replay_actions = await plugin._submit_attempt(
                    Context(),
                    payload,
                    "callback-2",
                    -1001,
                    77,
                    "rp1",
                    "a1",
                    int(attempt["answer_index"]),
                    "tok",
                )
                replay = next(action for action in replay_actions if action["type"] == "payout")
                self.assertEqual(replay["payout_key"], payout["payout_key"])
                self.assertEqual(plugin.storage.get_redpacket("rp1")["remaining_amount"], 0)

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
