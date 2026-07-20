from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import random
import sqlite3
import sys
import tempfile
import types
import unicodedata
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


def _install_telepilot_stubs() -> None:
    app = sys.modules.setdefault("app", types.ModuleType("app"))
    worker = sys.modules.setdefault("app.worker", types.ModuleType("app.worker"))
    plugins = sys.modules.setdefault("app.worker.plugins", types.ModuleType("app.worker.plugins"))
    command = types.ModuleType("app.worker.command")
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

    def current_command_prefix(*, fallback=None):
        return "。"

    async def resolve_public_sender_identity(
        ctx,
        *,
        chat_id,
        user_id,
        fallback_display_name="",
        unresolved_display_name="匿名用户",
        anonymous_admin_display_name="匿名管理员",
    ):
        identities = getattr(ctx, "identities", None)
        resolve = getattr(identities, "resolve", None) if identities is not None else None
        if callable(resolve):
            return await resolve(
                chat_id=chat_id,
                user_id=user_id,
                fallback_display_name=fallback_display_name,
                unresolved_display_name=unresolved_display_name,
                anonymous_admin_display_name=anonymous_admin_display_name,
            )
        client = getattr(ctx, "client", None)
        get_permissions = getattr(client, "get_permissions", None) if client is not None else None
        if not callable(get_permissions):
            return types.SimpleNamespace(
                user_id=user_id,
                display_name=fallback_display_name or str(user_id),
                is_anonymous_admin=False,
                tag=None,
                resolved=False,
            )
        try:
            permissions = await get_permissions(chat_id, user_id)
            participant = getattr(permissions, "participant", None)
            tag = str(getattr(participant, "rank", None) or "").strip() or None
            anonymous = bool(getattr(permissions, "anonymous", False))
            return types.SimpleNamespace(
                user_id=user_id,
                display_name=(tag or anonymous_admin_display_name) if anonymous else (fallback_display_name or str(user_id)),
                is_anonymous_admin=anonymous,
                tag=tag,
                resolved=True,
            )
        except Exception:
            return types.SimpleNamespace(
                user_id=user_id,
                display_name=unresolved_display_name,
                is_anonymous_admin=False,
                tag=None,
                resolved=False,
            )

    async def resolve_public_sender_identities(ctx, *, chat_id, senders, **kwargs):
        return {
            user_id: await resolve_public_sender_identity(
                ctx,
                chat_id=chat_id,
                user_id=user_id,
                fallback_display_name=display_name,
                **kwargs,
            )
            for user_id, display_name in senders.items()
        }

    def sanitize_public_display_name(value, *, limit=10, fallback="匿名用户"):
        invisible = {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0}
        visible = []
        for char in str(value or ""):
            codepoint = ord(char)
            category = unicodedata.category(char)
            if (
                char.isspace()
                or category in {"Cc", "Cf", "Cs", "Cn", "Zl", "Zp", "Zs"}
                or codepoint in invisible
                or 0x180B <= codepoint <= 0x180F
                or 0xFE00 <= codepoint <= 0xFE0F
                or 0xE0100 <= codepoint <= 0xE01EF
            ):
                continue
            visible.append(char)
        cleaned = "".join(visible)
        if limit is not None:
            cleaned = cleaned[: max(0, int(limit))]
        return cleaned or fallback

    base.Plugin = Plugin
    base.PluginContext = PluginContext
    base.register = register
    base.resolve_public_sender_identities = resolve_public_sender_identities
    base.resolve_public_sender_identity = resolve_public_sender_identity
    base.sanitize_public_display_name = sanitize_public_display_name
    command.current_command_prefix = current_command_prefix
    sys.modules["app.worker.command"] = command
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


def _action_html(action: dict[str, object]) -> str:
    rich_message = action.get("rich_message")
    if isinstance(rich_message, dict):
        return str(rich_message.get("html") or "")
    return str(action.get("text") or "")


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
    def test_static_manifest_matches_python_manifest(self) -> None:
        raw = json.loads(
            (Path(__file__).resolve().parents[1] / "ai_redpacket" / "plugin.json").read_text(encoding="utf-8")
        )

        self.assertEqual(raw["version"], manifest_module.PLUGIN_VERSION)
        self.assertEqual(raw["min_telepilot_version"], manifest_module.MANIFEST.min_telepilot_version)
        self.assertEqual(raw["usage"], manifest_module.USAGE)
        self.assertEqual(raw["config_schema"], manifest_module.CONFIG_SCHEMA)
        self.assertEqual(raw["event_subscriptions"], manifest_module.EVENT_SUBSCRIPTIONS)
        self.assertEqual(raw["interaction_entries"], manifest_module.INTERACTION_ENTRIES)

    def test_generation_and_user_limits_are_editable_in_supported_ranges(self) -> None:
        properties = manifest_module.CONFIG_SCHEMA["properties"]
        self.assertEqual(plugin_module.PLUGIN_VERSION, "0.1.30")
        self.assertEqual(manifest_module.PLUGIN_VERSION, "0.1.30")
        self.assertEqual(manifest_module.MANIFEST.min_telepilot_version, "0.70.9")
        self.assertEqual(properties["generation_count"]["default"], 200)
        self.assertEqual(properties["generation_count"]["minimum"], 100)
        self.assertEqual(properties["generation_count"]["maximum"], 500)
        self.assertEqual(properties["generation_concurrency"]["default"], 3)
        self.assertEqual(properties["generation_concurrency"]["minimum"], 1)
        self.assertEqual(properties["generation_concurrency"]["maximum"], 5)
        self.assertEqual(properties["generation_max_output_tokens"]["default"], 65536)
        self.assertEqual(properties["generation_max_output_tokens"]["minimum"], 4096)
        self.assertEqual(properties["generation_max_output_tokens"]["maximum"], 131072)
        self.assertEqual(properties["default_total_amount"]["default"], 150000)
        self.assertEqual(properties["default_questions"]["default"], 40)
        self.assertEqual(properties["reward_max"]["default"], 10000)
        self.assertEqual(properties["answer_timeout_seconds"]["default"], 300)
        self.assertIn("实际配置", properties["answer_timeout_seconds"]["description"])
        self.assertTrue(properties["weekly_auto_publish"]["default"])
        self.assertNotIn("readOnly", properties["daily_limit"])
        self.assertEqual(properties["daily_limit"]["maximum"], 100)
        self.assertNotIn("readOnly", properties["retry_count"])
        self.assertEqual(properties["retry_count"]["minimum"], 0)
        self.assertEqual(properties["retry_count"]["maximum"], 10)
        self.assertNotIn("readOnly", properties["question_bank_id"])
        self.assertNotIn("x-ui-hidden", properties["question_bank_id"])
        self.assertEqual(properties["question_bank_id"]["x-ui-widget"], "dynamic-select")
        self.assertEqual(properties["question_bank_id"]["x-ui-options-field"], "question_bank_options")
        self.assertTrue(properties["question_bank_options"]["x-ui-hidden"])
        self.assertTrue(properties["question_generation_prompt"]["x-ui-placeholder"].startswith("你是 TelePilot"))
        self.assertIn("reset [用户ID]", manifest_module.USAGE)
        self.assertIn("reset all", manifest_module.USAGE)
        self.assertIn("自动删除原命令消息", manifest_module.USAGE)
        for preview_key in (
            "packet_message_preview",
            "question_message_preview",
            "success_message_preview",
            "failed_message_preview",
            "settlement_message_preview",
            "reminder_message_preview",
            "weekly_message_preview",
        ):
            self.assertIn(preview_key, properties)
        self.assertIn("weekly_message_template", properties)
        self.assertIn("settlement_message_template", properties)
        self.assertIn("reminder_message_template", properties)
        self.assertIn("send_rich_message", manifest_module.INTERACTION_ENTRIES[0]["result_contract"]["actions"])
        public_list = next(
            item
            for item in manifest_module.EVENT_SUBSCRIPTIONS
            if item.get("filters") == {"keywords": ["/airp list"]}
        )
        self.assertEqual(public_list["events"], ["message"])
        self.assertEqual(public_list["source"], ["interaction_bot"])
        self.assertEqual(public_list["scope"], "all_allowed_chats")

    def test_legacy_database_is_migrated_to_context_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            legacy_path = root / "installed" / "ai_redpacket.sqlite3"
            legacy_storage = storage_module.AIStorage(legacy_path)
            legacy_storage.replace_bank(
                account_id=1,
                bank_id="persistent-bank",
                title="持久题库",
                questions=_questions(3),
            )

            class Context:
                data_dir = root / "plugin-data"

            with patch.object(plugin_module, "DATA_PATH", legacy_path):
                plugin = plugin_module.AIRedpacketPlugin()
                plugin._ensure_storage(Context())

            self.assertIsNotNone(plugin.storage)
            self.assertEqual(plugin.storage.path, Context.data_dir / "ai_redpacket.sqlite3")
            self.assertEqual(len(plugin.storage.get_bank_questions(1, "persistent-bank")), 3)

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
            "generation_max_output_tokens": ("AI 生成", 2),
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

    def test_extract_question_batch_salvages_complete_jsonl_before_truncated_tail(self) -> None:
        rows = [json.dumps({"title": "JSONL 题库"}, ensure_ascii=False)]
        rows.extend(json.dumps(item, ensure_ascii=False) for item in _questions(2))
        rows.append('{"question":"未完成')

        data = plugin_module.extract_question_batch("\n".join(rows))

        self.assertEqual(data["title"], "JSONL 题库")
        self.assertEqual(len(data["questions"]), 2)

    def test_source_excerpt_caps_single_context_and_shards_multiple_large_batches(self) -> None:
        source = "".join(f"{index:06d}" for index in range(30_000))

        single_excerpt = plugin_module.source_excerpt_for_batch(source, 0, 1)
        self.assertLessEqual(len(single_excerpt), plugin_module.MAX_BATCH_SOURCE_CHARS)
        self.assertTrue(single_excerpt.startswith(source[:100]))
        self.assertTrue(single_excerpt.endswith(source[-100:]))
        excerpts = [plugin_module.source_excerpt_for_batch(source, index, 3) for index in range(3)]

        self.assertTrue(all(len(excerpt) < len(source) for excerpt in excerpts))
        self.assertEqual(len(set(excerpts)), 3)
        self.assertEqual(excerpts[0], source[: len(excerpts[0])])
        self.assertEqual(excerpts[-1], source[-len(excerpts[-1]) :])


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
            self.assertEqual(version, 7)
            self.assertIn("settled_at", redpacket_columns)
            self.assertIn("user_display_name", attempt_columns)
            with storage.connect() as conn:
                reminder_table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'redpacket_daily_reminder'"
                ).fetchone()
            self.assertIsNotNone(reminder_table)
            self.assertEqual(storage.get_source_cache(1, "https://example.com/source")["content"], "缓存正文")

    def test_version_five_global_resets_are_discarded_without_guessing_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ai_redpacket.sqlite3"
            storage = storage_module.AIStorage(path)
            storage.replace_bank(account_id=1, bank_id="bank", title="测试题库", questions=_questions(1))
            for packet_id, chat_id in (("chat-a", -1001), ("chat-b", -2002)):
                storage.create_redpacket(
                    redpacket_id=packet_id,
                    account_id=1,
                    chat_id=chat_id,
                    creator_id=99,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )
                storage.reserve_question(
                    attempt_id=f"attempt-{packet_id}",
                    account_id=1,
                    user_id=77,
                    chat_id=chat_id,
                    redpacket_id=packet_id,
                    date="2026-07-16",
                    submission_token=f"token-{packet_id}",
                    reservation_seconds=300,
                )

            with storage.connect() as conn:
                conn.executescript(
                    """
                    DROP TABLE redpacket_limit_reset;
                    CREATE TABLE redpacket_limit_reset (
                        account_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        reset_at REAL NOT NULL,
                        PRIMARY KEY(account_id, user_id, date)
                    );
                    INSERT INTO redpacket_limit_reset(account_id, user_id, date, reset_at)
                    VALUES (1, 77, '2026-07-16', 12345);
                    UPDATE schema_meta SET version = 5;
                    """
                )

            migrated = storage_module.AIStorage(path)
            with migrated.connect() as conn:
                version = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()["version"]
                rows = conn.execute(
                    """
                    SELECT chat_id, reset_at FROM redpacket_limit_reset
                    WHERE account_id = 1 AND user_id = 77 AND date = '2026-07-16'
                    ORDER BY chat_id
                    """
                ).fetchall()
            self.assertEqual(version, 7)
            self.assertEqual(rows, [])

    def test_version_six_resets_are_cleared_without_touching_attempts_or_rewards(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ai_redpacket.sqlite3"
            storage = storage_module.AIStorage(path)
            storage.replace_bank(account_id=1, bank_id="bank", title="测试题库", questions=_questions(1))
            storage.create_redpacket(
                redpacket_id="packet",
                account_id=1,
                chat_id=-1001,
                creator_id=99,
                bank_id="bank",
                total_amount=10,
                rewards=[10],
                ttl_seconds=3600,
            )
            reserved = storage.reserve_question(
                attempt_id="attempt",
                account_id=1,
                user_id=77,
                chat_id=-1001,
                redpacket_id="packet",
                date="2026-07-16",
                submission_token="token",
                reservation_seconds=300,
            )
            with storage.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO redpacket_limit_reset(account_id, chat_id, user_id, date, reset_at)
                    VALUES (1, -1001, 77, '2026-07-16', 12345)
                    """
                )
                conn.execute(
                    "UPDATE redpacket_attempt SET attempts = 1, success = 1, reward = 10 WHERE id = ?",
                    (reserved["id"],),
                )
                conn.execute(
                    "UPDATE redpacket SET remaining_amount = 0 WHERE id = 'packet'"
                )
                conn.execute("UPDATE schema_meta SET version = 6")

            migrated = storage_module.AIStorage(path)
            with migrated.connect() as conn:
                version = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()["version"]
                reset_count = conn.execute("SELECT COUNT(*) FROM redpacket_limit_reset").fetchone()[0]
                attempt = conn.execute(
                    "SELECT id, reward, success FROM redpacket_attempt WHERE id = ?",
                    (reserved["id"],),
                ).fetchone()
                packet = conn.execute(
                    "SELECT total_amount, remaining_amount FROM redpacket WHERE id = 'packet'"
                ).fetchone()

            self.assertEqual(version, 7)
            self.assertEqual(reset_count, 0)
            self.assertIsNotNone(attempt)
            self.assertEqual((int(attempt["reward"]), int(attempt["success"])), (10, 1))
            self.assertEqual((int(packet["total_amount"]), int(packet["remaining_amount"])), (10, 0))

    def test_version_five_reset_migration_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ai_redpacket.sqlite3"
            storage = storage_module.AIStorage(path)
            with storage.connect() as conn:
                conn.executescript(
                    """
                    DROP TABLE redpacket_limit_reset;
                    CREATE TABLE redpacket_limit_reset (
                        account_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        reset_at REAL NOT NULL,
                        PRIMARY KEY(account_id, user_id, date)
                    );
                    INSERT INTO redpacket_limit_reset(account_id, user_id, date, reset_at)
                    VALUES (1, 77, '2026-07-16', 12345);
                    UPDATE schema_meta SET version = 5;
                    """
                )

            class InterruptedMigrationStorage(storage_module.AIStorage):
                def connect(self):
                    conn = super().connect()

                    def deny_legacy_drop(action, arg1, _arg2, _database, _trigger):
                        if action == sqlite3.SQLITE_DROP_TABLE and arg1 == "redpacket_limit_reset_legacy":
                            return sqlite3.SQLITE_DENY
                        return sqlite3.SQLITE_OK

                    conn.set_authorizer(deny_legacy_drop)
                    return conn

            with self.assertRaises(sqlite3.DatabaseError):
                InterruptedMigrationStorage(path)

            with sqlite3.connect(path) as conn:
                current_columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(redpacket_limit_reset)").fetchall()
                }
                legacy_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'redpacket_limit_reset_legacy'"
                ).fetchone()
                version = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()[0]
                reset_count = conn.execute("SELECT COUNT(*) FROM redpacket_limit_reset").fetchone()[0]

            self.assertNotIn("chat_id", current_columns)
            self.assertIsNone(legacy_table)
            self.assertEqual(version, 5)
            self.assertEqual(reset_count, 1)


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

    def test_each_new_packet_samples_a_fresh_question_batch(self) -> None:
        self.storage.replace_bank(
            account_id=1,
            bank_id="bank",
            title="测试题库",
            questions=_questions(100),
        )
        original_connect = self.storage.connect
        connection_index = 0

        def deterministic_connect():
            nonlocal connection_index
            connection_index += 1
            conn = original_connect()
            if connection_index in {1, 3}:
                random_call = 0
                direction = 1 if connection_index == 1 else -1

                def deterministic_random() -> int:
                    nonlocal random_call
                    random_call += 1
                    return direction * random_call

                conn.create_function("RANDOM", 0, deterministic_random)
            return conn

        with patch.object(self.storage, "connect", side_effect=deterministic_connect):
            self._create_packet("fresh-a", 20)
            self._create_packet("fresh-b", 20)

        with self.storage.connect() as conn:
            first = {
                int(row[0])
                for row in conn.execute(
                    "SELECT question_bank_id FROM redpacket_question WHERE redpacket_id = ?",
                    ("fresh-a",),
                )
            }
            second = {
                int(row[0])
                for row in conn.execute(
                    "SELECT question_bank_id FROM redpacket_question WHERE redpacket_id = ?",
                    ("fresh-b",),
                )
            }

        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 20)
        self.assertTrue(first.isdisjoint(second))

    def test_hard_expiration_caps_ttl_without_extending_shorter_ttl(self) -> None:
        created_at = datetime.fromisoformat("2026-07-14T20:00:00+08:00").timestamp()
        next_morning = datetime.fromisoformat("2026-07-15T08:30:00+08:00").timestamp()
        with patch.object(storage_module.time, "time", return_value=created_at):
            capped = self.storage.create_redpacket(
                redpacket_id="hard-expiration",
                account_id=1,
                chat_id=-1001,
                creator_id=99,
                bank_id="bank",
                total_amount=10,
                rewards=[10],
                ttl_seconds=86_400,
                hard_expires_at=next_morning,
            )
            shorter = self.storage.create_redpacket(
                redpacket_id="short-expiration",
                account_id=1,
                chat_id=-1001,
                creator_id=99,
                bank_id="bank",
                total_amount=10,
                rewards=[10],
                ttl_seconds=3_600,
                hard_expires_at=next_morning,
            )

        self.assertEqual(capped["expires_at"], next_morning)
        self.assertEqual(shorter["expires_at"], created_at + 3_600)

    def test_existing_expiration_can_only_be_shortened(self) -> None:
        created_at = datetime.fromisoformat("2026-07-14T10:00:00+08:00").timestamp()
        with patch.object(storage_module.time, "time", return_value=created_at):
            self._create_packet("shorten-expiration", 1)
        original = self.storage.get_redpacket("shorten-expiration")["expires_at"]

        self.storage.shorten_redpacket_expiration(1, "shorten-expiration", original + 10_000)
        self.assertEqual(self.storage.get_redpacket("shorten-expiration")["expires_at"], original)
        self.storage.shorten_redpacket_expiration(1, "shorten-expiration", original - 1_000)
        self.assertEqual(self.storage.get_redpacket("shorten-expiration")["expires_at"], original - 1_000)

    def test_unfinished_yesterday_packets_are_filtered_and_aggregated(self) -> None:
        yesterday = datetime.fromisoformat("2026-07-14T12:00:00+08:00").timestamp()
        today = datetime.fromisoformat("2026-07-15T01:00:00+08:00").timestamp()
        fired_at = datetime.fromisoformat("2026-07-15T08:00:00+08:00").timestamp()
        with patch.object(storage_module.time, "time", return_value=yesterday):
            for packet_id in ("unfinished-yesterday", "finished-yesterday", "empty-yesterday"):
                self.storage.create_redpacket(
                    redpacket_id=packet_id,
                    account_id=1,
                    chat_id=-1001,
                    creator_id=99,
                    bank_id="bank",
                    total_amount=20,
                    rewards=[10, 10],
                    ttl_seconds=100_000,
                )
            self.storage.create_redpacket(
                redpacket_id="expired-yesterday",
                account_id=1,
                chat_id=-1001,
                creator_id=99,
                bank_id="bank",
                total_amount=10,
                rewards=[10],
                ttl_seconds=60,
            )
        with patch.object(storage_module.time, "time", return_value=today):
            self.storage.create_redpacket(
                redpacket_id="unfinished-today",
                account_id=1,
                chat_id=-1001,
                creator_id=99,
                bank_id="bank",
                total_amount=10,
                rewards=[10],
                ttl_seconds=100_000,
            )
        with self.storage.transaction() as conn:
            conn.execute("UPDATE redpacket SET status = 'finished' WHERE id = 'finished-yesterday'")
            conn.execute("UPDATE redpacket SET remaining_amount = 0 WHERE id = 'empty-yesterday'")
            conn.execute("UPDATE redpacket SET remaining_amount = 10 WHERE id = 'unfinished-yesterday'")
            conn.execute(
                """
                UPDATE redpacket_question SET claimed_by = 501
                WHERE id = (
                    SELECT MIN(id) FROM redpacket_question WHERE redpacket_id = 'unfinished-yesterday'
                )
                """
            )

        packets = self.storage.unfinished_redpackets_created_between(
            1,
            datetime.fromisoformat("2026-07-14T00:00:00+08:00").timestamp(),
            datetime.fromisoformat("2026-07-15T00:00:00+08:00").timestamp(),
            now=fired_at,
        )

        self.assertEqual([packet["id"] for packet in packets], ["unfinished-yesterday"])
        self.assertEqual(packets[0]["claimed_count"], 1)
        self.assertEqual(packets[0]["claimed_amount"], 10)
        self.assertEqual(self.storage.get_redpacket("expired-yesterday")["status"], "expired")

    def test_daily_reminder_mark_is_idempotent(self) -> None:
        self.assertFalse(self.storage.daily_reminder_published(1, -1001, "2026-07-14"))
        self.storage.mark_daily_reminder_published(1, -1001, "2026-07-14", published_at=1.0)
        self.storage.mark_daily_reminder_published(1, -1001, "2026-07-14", published_at=2.0)
        self.assertTrue(self.storage.daily_reminder_published(1, -1001, "2026-07-14"))
        with self.storage.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM redpacket_daily_reminder").fetchone()["count"]
        self.assertEqual(count, 1)

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
        with self.assertRaisesRegex(storage_module.StorageError, "答题按钮已经失效"):
            self.storage.submit_answer(
                attempt_id="attempt-101",
                account_id=1,
                user_id=101,
                chat_id=-1001,
                redpacket_id="packet",
                option_index=wrong,
                submission_token="token-101",
                submission_key="double-click-with-new-callback-id",
                retry_count=1,
            )
        second = self.storage.submit_answer(
            attempt_id="attempt-101",
            account_id=1,
            user_id=101,
            chat_id=-1001,
            redpacket_id="packet",
            option_index=wrong,
            submission_token=str(first["submission_token"]),
            submission_key="submit-2",
            retry_count=1,
        )
        self.assertFalse(second["correct"])
        self.assertTrue(second["finished"])
        self.assertEqual(second["attempts"], 2)
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
        results = []
        token = "retry-token"
        for index in range(3):
            result = self.storage.submit_answer(
                attempt_id="retry-attempt",
                account_id=1,
                user_id=151,
                chat_id=-1001,
                redpacket_id="retry-packet",
                option_index=wrong,
                submission_token=token,
                submission_key=f"retry-submit-{index}",
                retry_count=2,
                daily_limit=1,
                reservation_seconds=45,
            )
            results.append(result)
            if not result["finished"]:
                remaining = float(result["reserved_until"]) - datetime.now().timestamp()
                self.assertGreaterEqual(remaining, 44)
                self.assertLessEqual(remaining, 46)
            token = str(result["submission_token"])
        self.assertFalse(results[0]["finished"])
        self.assertFalse(results[1]["finished"])
        self.assertTrue(results[2]["finished"])
        self.assertEqual(results[2]["max_attempts"], 3)

    def test_answer_click_cooldown_does_not_consume_retry(self) -> None:
        self._create_packet("cooldown-packet", 1)
        attempt = self.storage.reserve_question(
            attempt_id="cooldown-attempt",
            account_id=1,
            user_id=159,
            chat_id=-1001,
            redpacket_id="cooldown-packet",
            date="2026-07-14",
            submission_token="cooldown-token",
            reservation_seconds=300,
        )
        wrong = (int(attempt["answer_index"]) + 1) % 3
        first = self.storage.submit_answer(
            attempt_id="cooldown-attempt",
            account_id=1,
            user_id=159,
            chat_id=-1001,
            redpacket_id="cooldown-packet",
            option_index=wrong,
            submission_token="cooldown-token",
            submission_key="cooldown-first",
            retry_count=1,
            answer_cooldown_seconds=2,
        )

        with self.assertRaisesRegex(storage_module.StorageError, "点击太快"):
            self.storage.submit_answer(
                attempt_id="cooldown-attempt",
                account_id=1,
                user_id=159,
                chat_id=-1001,
                redpacket_id="cooldown-packet",
                option_index=wrong,
                submission_token=str(first["submission_token"]),
                submission_key="cooldown-second-too-fast",
                retry_count=1,
                answer_cooldown_seconds=2,
            )

        with self.storage.connect() as conn:
            row = conn.execute(
                "SELECT attempts, submission_token FROM redpacket_attempt WHERE id = ?",
                ("cooldown-attempt",),
            ).fetchone()
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["submission_token"], first["submission_token"])

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

        self.storage.reset_daily_limit(1, -1001, 152, "2026-07-14")
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

    def test_admin_reset_all_marks_every_participant_for_only_the_selected_day(self) -> None:
        self._create_packet("reset-all", 3)
        for user_id, date in ((153, "2026-07-14"), (154, "2026-07-14"), (155, "2026-07-13")):
            self.storage.reserve_question(
                attempt_id=f"reset-all-attempt-{user_id}",
                account_id=1,
                user_id=user_id,
                chat_id=-1001,
                redpacket_id="reset-all",
                date=date,
                submission_token=f"reset-all-token-{user_id}",
                reservation_seconds=300,
            )
        self.storage.create_redpacket(
            redpacket_id="reset-all-other-chat",
            account_id=1,
            chat_id=-2002,
            creator_id=99,
            bank_id="bank",
            total_amount=10,
            rewards=[10],
            ttl_seconds=3600,
        )
        self.storage.reserve_question(
            attempt_id="reset-all-other-chat-attempt",
            account_id=1,
            user_id=156,
            chat_id=-2002,
            redpacket_id="reset-all-other-chat",
            date="2026-07-14",
            submission_token="reset-all-other-chat-token",
            reservation_seconds=300,
        )

        result = self.storage.reset_all_daily_limits(1, -1001, "2026-07-14")

        self.assertEqual(result["user_count"], 2)
        with self.storage.connect() as conn:
            reset_users = conn.execute(
                """
                SELECT user_id FROM redpacket_limit_reset
                WHERE account_id = ? AND chat_id = ? AND date = ? ORDER BY user_id
                """,
                (1, -1001, "2026-07-14"),
            ).fetchall()
            attempt_count = conn.execute(
                "SELECT COUNT(*) AS count FROM redpacket_attempt WHERE account_id = ?",
                (1,),
            ).fetchone()["count"]
        self.assertEqual([int(row["user_id"]) for row in reset_users], [153, 154])
        self.assertEqual(attempt_count, 4)
        with self.storage.connect() as conn:
            other_chat_reset = conn.execute(
                """
                SELECT 1 FROM redpacket_limit_reset
                WHERE account_id = ? AND chat_id = ? AND user_id = ? AND date = ?
                """,
                (1, -2002, 156, "2026-07-14"),
            ).fetchone()
        self.assertIsNone(other_chat_reset)

    def test_single_reset_only_unlocks_the_selected_chat(self) -> None:
        for packet_id, chat_id in (("scope-b-first", -2002), ("scope-a", -1001), ("scope-b-second", -2002)):
            self.storage.create_redpacket(
                redpacket_id=packet_id,
                account_id=1,
                chat_id=chat_id,
                creator_id=99,
                bank_id="bank",
                total_amount=10,
                rewards=[10],
                ttl_seconds=3600,
            )
        first = self.storage.reserve_question(
            attempt_id="scope-b-first-attempt",
            account_id=1,
            user_id=157,
            chat_id=-2002,
            redpacket_id="scope-b-first",
            date="2026-07-14",
            submission_token="scope-b-first-token",
            reservation_seconds=300,
        )
        self.storage.submit_answer(
            attempt_id="scope-b-first-attempt",
            account_id=1,
            user_id=157,
            chat_id=-2002,
            redpacket_id="scope-b-first",
            option_index=int(first["answer_index"]),
            submission_token="scope-b-first-token",
            submission_key="scope-b-first-submit",
            retry_count=1,
            daily_limit=1,
        )

        self.storage.reset_daily_limit(1, -1001, 157, "2026-07-14")

        unlocked = self.storage.reserve_question(
            attempt_id="scope-a-attempt",
            account_id=1,
            user_id=157,
            chat_id=-1001,
            redpacket_id="scope-a",
            date="2026-07-14",
            submission_token="scope-a-token",
            reservation_seconds=300,
        )
        self.assertEqual(unlocked["id"], "scope-a-attempt")
        with self.assertRaisesRegex(storage_module.StorageError, "今天的红包挑战已经结束"):
            self.storage.reserve_question(
                attempt_id="scope-b-second-attempt",
                account_id=1,
                user_id=157,
                chat_id=-2002,
                redpacket_id="scope-b-second",
                date="2026-07-14",
                submission_token="scope-b-second-token",
                reservation_seconds=300,
            )

    def test_unanswered_timeout_releases_question_without_consuming_attempt(self) -> None:
        self._create_packet("timeout-return", 1)
        attempt = self.storage.reserve_question(
            attempt_id="timeout-attempt",
            account_id=1,
            user_id=156,
            chat_id=-1001,
            redpacket_id="timeout-return",
            date="2026-07-14",
            submission_token="timeout-token",
            reservation_seconds=30,
        )
        with self.storage.transaction() as conn:
            conn.execute(
                "UPDATE redpacket_question SET reserved_until = ? WHERE id = ?",
                (datetime.now().timestamp() - 1, int(attempt["question_slot_id"])),
            )
        with self.assertRaisesRegex(storage_module.StorageError, "题目答题时间已结束"):
            self.storage.submit_answer(
                attempt_id="timeout-attempt",
                account_id=1,
                user_id=156,
                chat_id=-1001,
                redpacket_id="timeout-return",
                option_index=int(attempt["answer_index"]),
                submission_token="timeout-token",
                submission_key="timeout-submit",
                retry_count=1,
                reservation_seconds=30,
            )

        expired = self.storage.expire_unanswered_attempt(
            account_id=1,
            attempt_id="timeout-attempt",
            user_id=156,
            submission_token="timeout-token",
            now=float(attempt["reserved_until"]) + 1,
        )

        self.assertTrue(expired["expired"])
        with self.storage.connect() as conn:
            old_attempt = conn.execute(
                "SELECT 1 FROM redpacket_attempt WHERE id = ?",
                ("timeout-attempt",),
            ).fetchone()
            slot = conn.execute(
                "SELECT reserved_by, claimed_by FROM redpacket_question WHERE redpacket_id = ?",
                ("timeout-return",),
            ).fetchone()
        self.assertIsNone(old_attempt)
        self.assertIsNone(slot["reserved_by"])
        self.assertIsNone(slot["claimed_by"])

        retried = self.storage.reserve_question(
            attempt_id="timeout-attempt-retry",
            account_id=1,
            user_id=156,
            chat_id=-1001,
            redpacket_id="timeout-return",
            date="2026-07-14",
            submission_token="timeout-token-retry",
            reservation_seconds=30,
        )
        self.assertEqual(retried["attempts"], 0)

    def test_single_slot_cannot_be_reserved_concurrently(self) -> None:
        self._create_packet(count=1)
        self._reserve(201)
        with self.assertRaisesRegex(storage_module.StorageError, "暂时没有可领取"):
            self._reserve(202)

    def test_active_packet_list_excludes_completed_and_reports_claim_progress(self) -> None:
        self._create_packet("active-list", 2)
        attempt = self._reserve(203, "active-list")
        self.storage.submit_answer(
            attempt_id="attempt-203",
            account_id=1,
            user_id=203,
            chat_id=-1001,
            redpacket_id="active-list",
            option_index=int(attempt["answer_index"]),
            submission_token="token-203",
            submission_key="active-list-success",
            retry_count=1,
        )
        self._create_packet("closed-list", 1)
        self.assertTrue(self.storage.close_redpacket(1, -1001, "closed-list"))
        self._create_packet("expired-list", 1)
        with self.storage.transaction() as conn:
            conn.execute("UPDATE redpacket SET expires_at = ? WHERE id = ?", (1.0, "expired-list"))

        packets = self.storage.list_active_redpackets(1, -1001, now=2.0)

        self.assertEqual([packet["id"] for packet in packets], ["active-list"])
        self.assertEqual(packets[0]["claimed_count"], 1)
        self.assertEqual(packets[0]["claimed_amount"], 10)
        self.assertEqual(packets[0]["remaining_amount"], 10)
        self.assertEqual(self.storage.get_redpacket("expired-list")["status"], "expired")

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

    def test_usage_uses_live_system_command_prefix(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Context:
            config = {"command": "airp"}
            account_config = {"command_prefix": ","}

        usage = plugin._usage(Context())
        self.assertIn("。airp reset [用户ID]", usage)
        self.assertIn("。airp reset all", usage)
        self.assertIn("。airp-7", usage)
        self.assertIn("自动删除原命令消息", usage)
        self.assertNotIn(",airp", usage)

    def test_sender_display_name_prefers_name_then_username(self) -> None:
        named = plugin_module.sender_display_name(
            {"sender": {"user_id": 77, "display_name": "张三", "username": "zhangsan"}},
            77,
        )
        username_only = plugin_module.sender_display_name(
            {"sender": {"user_id": 78, "display_name": "  ", "username": "lisi"}},
            78,
        )
        self.assertEqual(named, "张三")
        self.assertEqual(username_only, "@lisi")

    def test_display_name_removes_unicode_invisible_characters_before_truncation(self) -> None:
        name = "\u206a\u200c\u200f\u206a \u206a\u200c\u200f\u206a人"

        self.assertEqual(plugin_module.truncate_display_name(name), "人")

    def test_display_name_removes_all_unicode_whitespace_and_blank_glyphs(self) -> None:
        self.assertEqual(
            plugin_module.truncate_display_name(" 张\u00a0三\u3000\t\u2800\u3164\uffa0 "),
            "张三",
        )

    def test_invisible_only_display_name_falls_back_to_username_or_anonymous(self) -> None:
        invisible = "\u206a\u200c\u200f\u2800\u3164\uffa0\u034f"

        self.assertEqual(plugin_module.truncate_display_name(invisible), "匿名用户")
        self.assertEqual(
            plugin_module.sender_display_name(
                {"sender": {"user_id": 79, "display_name": invisible, "username": "visible_user"}},
                79,
            ),
            "@visible_u",
        )

    def test_anonymous_admin_is_blocked_from_all_participation_callbacks(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="rp-anonymous",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )

                class Client:
                    async def get_permissions(self, chat_id, user_id):
                        return types.SimpleNamespace(
                            anonymous=True,
                            participant=types.SimpleNamespace(rank="值班管理员"),
                        )

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    client = Client()
                    scheduler = None

                payload = {
                    "source": {"type": "callback_query"},
                    "message": {"chat_id": -1001, "message_id": 88},
                    "sender": {"user_id": 77, "display_name": "真实姓名", "username": "real_user"},
                }
                callbacks = (
                    "airp:start:rp-anonymous",
                    "airp:answer:rp-anonymous:attempt:0:token",
                    "airp:repay:rp-anonymous:attempt",
                    "airp:repayme:rp-anonymous",
                )
                for index, callback_data in enumerate(callbacks):
                    actions = await plugin._handle_callback(
                        Context(),
                        payload,
                        f"callback-anonymous-{index}",
                        callback_data,
                    )
                    self.assertEqual([action["type"] for action in actions], ["answer_callback"])
                    self.assertTrue(actions[0]["show_alert"])
                    self.assertEqual(actions[0]["text"], plugin_module.ANONYMOUS_ADMIN_BLOCKED_TEXT)
                with plugin.storage.connect() as conn:
                    attempt_count = conn.execute(
                        "SELECT COUNT(*) FROM redpacket_attempt WHERE user_id = ?",
                        (77,),
                    ).fetchone()[0]
                self.assertEqual(attempt_count, 0)

        asyncio.run(run_case())

    def test_non_anonymous_admin_can_start_question(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="rp-visible-admin",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )

                class Client:
                    async def get_permissions(self, chat_id, user_id):
                        return types.SimpleNamespace(
                            anonymous=False,
                            participant=types.SimpleNamespace(rank="值班管理员"),
                        )

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    client = Client()
                    scheduler = None

                actions = await plugin._handle_callback(
                    Context(),
                    {
                        "source": {"type": "callback_query"},
                        "message": {"chat_id": -1001, "message_id": 88},
                        "sender": {"user_id": 77, "display_name": "公开姓名", "username": "visible_admin"},
                    },
                    "callback-visible-admin",
                    "airp:start:rp-visible-admin",
                )

                question = next(action for action in actions if action["type"] == "send_rich_message")
                self.assertIn("公开姓名 这是你的专属雨露", _action_html(question))
                self.assertNotIn("值班管理员 这是你的专属雨露", _action_html(question))

        asyncio.run(run_case())

    def test_unresolved_identity_is_not_saved_as_anonymous_user(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="rp-unresolved",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )

                class Identities:
                    calls = 0

                    async def resolve(self, **kwargs):
                        self.calls += 1
                        return types.SimpleNamespace(
                            display_name="匿名用户",
                            is_anonymous_admin=False,
                            resolved=False,
                        )

                identities = Identities()
                logs = []

                async def log(level, message, **detail):
                    logs.append((level, message, detail))

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    client = None
                    scheduler = None

                context = Context()
                context.identities = identities
                context.log = log
                actions = await plugin._handle_callback(
                    context,
                    {
                        "source": {"type": "callback_query"},
                        "message": {"chat_id": -1001, "message_id": 88},
                        "sender": {"user_id": 77, "display_name": "公开姓名"},
                    },
                    "callback-unresolved",
                    "airp:start:rp-unresolved",
                )

                self.assertEqual(identities.calls, 1)
                self.assertEqual([action["type"] for action in actions], ["answer_callback"])
                self.assertEqual(actions[0]["text"], plugin_module.IDENTITY_VERIFICATION_FAILED_TEXT)
                self.assertEqual(
                    logs,
                    [
                        (
                            "info",
                            "AI 红包用户身份暂未核验",
                            {
                                "chat_id": -1001,
                                "user_id": 77,
                                "redpacket_id": "rp-unresolved",
                                "callback_action": "start",
                            },
                        )
                    ],
                )
                with plugin.storage.connect() as conn:
                    attempts = conn.execute("SELECT COUNT(*) FROM redpacket_attempt").fetchone()[0]
                self.assertEqual(attempts, 0)

        asyncio.run(run_case())

    def test_terminal_packet_callbacks_skip_identity_verification(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                for packet_id in ("rp-finished", "rp-closed", "rp-expired"):
                    plugin.storage.create_redpacket(
                        redpacket_id=packet_id,
                        account_id=1,
                        chat_id=-1001,
                        creator_id=1,
                        bank_id="bank",
                        total_amount=10,
                        rewards=[10],
                        ttl_seconds=3600,
                    )
                plugin.storage.close_redpacket(1, -1001, "rp-closed")
                with plugin.storage.transaction() as conn:
                    conn.execute("UPDATE redpacket SET status = 'finished' WHERE id = 'rp-finished'")
                    conn.execute("UPDATE redpacket SET expires_at = 0 WHERE id = 'rp-expired'")

                class Identities:
                    calls = 0

                    async def resolve(self, **kwargs):
                        self.calls += 1
                        raise AssertionError("终态红包不应调用身份核验")

                identities = Identities()
                logs = []

                async def log(level, message, **detail):
                    logs.append((level, message, detail))

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    client = None
                    scheduler = None

                context = Context()
                context.identities = identities
                context.log = log
                payload = {
                    "source": {"type": "callback_query"},
                    "message": {"chat_id": -1001, "message_id": 88},
                    "sender": {"user_id": 77, "display_name": "公开姓名"},
                }
                cases = (
                    ("rp-finished", "红包已经结束"),
                    ("rp-closed", "红包已经结束"),
                    ("rp-expired", "红包已经过期"),
                )
                for packet_id, expected_text in cases:
                    for callback_data in (
                        f"airp:start:{packet_id}",
                        f"airp:answer:{packet_id}:old-attempt:0:old-token",
                    ):
                        actions = await plugin._handle_callback(
                            context,
                            payload,
                            f"callback-{packet_id}",
                            callback_data,
                        )
                        self.assertEqual([action["type"] for action in actions], ["answer_callback"])
                        self.assertEqual(actions[0]["text"], expected_text)

                self.assertEqual(identities.calls, 0)
                self.assertEqual(logs, [])
                with plugin.storage.connect() as conn:
                    attempts = conn.execute("SELECT COUNT(*) FROM redpacket_attempt").fetchone()[0]
                self.assertEqual(attempts, 0)

        asyncio.run(run_case())

    def test_create_auto_adjusts_incompatible_reward_bounds_from_command(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(
                    account_id=1,
                    bank_id="0e90376c7a2b",
                    title="命令题库",
                    questions=_questions(5),
                )

                class Context:
                    account_id = 1
                    config = {"reward_min": 123, "reward_max": 13579}
                    account_config = {}
                    log = None

                actions = await plugin._create_packet(
                    Context(),
                    -1001,
                    77,
                    1,
                    ["200", "2", "0e90376c7a2b"],
                )
                self.assertEqual(actions[0]["type"], "send_rich_message")
                self.assertNotIn("红包创建失败", _action_html(actions[0]))
                packet = plugin.storage.list_redpackets(1, -1001)[0]
                with plugin.storage.connect() as conn:
                    rewards = [
                        int(row["reward"])
                        for row in conn.execute(
                            "SELECT reward FROM redpacket_question WHERE redpacket_id = ?",
                            (packet["id"],),
                        ).fetchall()
                    ]
                self.assertEqual(sum(rewards), 200)
                self.assertEqual(len(rewards), 2)
                self.assertNotEqual(rewards[0], rewards[1])
                self.assertGreaterEqual(min(rewards), 1)

        asyncio.run(run_case())

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
                self.assertEqual(actions[0]["type"], "send_rich_message")
                self.assertEqual(actions[0]["reply_to_message_id"], 9)
                self.assertIn("<details>", _action_html(actions[0]))
                self.assertIn("AI 红包本周排行榜", _action_html(actions[0]))

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

    def test_question_timeout_edits_returns_and_deletes_question_message(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="timeout-message",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )
                applied = []
                delays = []

                async def fake_sleep(delay):
                    delays.append(delay)

                class Messages:
                    async def read_saved_message_id(self, key):
                        return 88

                    async def apply(self, actions, **kwargs):
                        applied.append((actions, kwargs))

                class Context:
                    account_id = 1
                    config = {"answer_timeout_seconds": 30}
                    account_config = {}
                    messages = Messages()
                    log = None

                with patch.object(plugin_module.asyncio, "sleep", fake_sleep):
                    actions = plugin._start_attempt(Context(), "callback", -1001, 77, "张三", "timeout-message")
                    question = actions[1]
                    self.assertEqual(question["send_via"], "interaction_bot")
                    self.assertIn("save_message_id_key", question)
                    timeout_job_id = next(
                        job_id for job_id in plugin._timer_tasks if job_id.startswith("question_timeout_")
                    )
                    attempt_id = timeout_job_id.removeprefix("question_timeout_")
                    await plugin._timer_tasks[timeout_job_id]
                    pending = list(plugin._timer_tasks.values())
                    if pending:
                        await asyncio.gather(*pending)

                edit = applied[0][0][0]
                self.assertEqual(edit["type"], "edit_message")
                self.assertEqual(edit["message_id"], 88)
                self.assertNotIn("text", edit)
                self.assertIn("<h1>答题超时</h1>", _action_html(edit))
                self.assertIn("30 秒内未作答", _action_html(edit))
                self.assertIn("已回归题库", _action_html(edit))
                self.assertIn("不消耗领取与答题次数", _action_html(edit))
                self.assertEqual(edit["reply_markup"], {"inline_keyboard": []})
                self.assertGreaterEqual(delays[0], 29)
                self.assertLessEqual(delays[0], 31)
                self.assertIn(5, delays)

                deleted = applied[1][0][0]
                self.assertEqual(deleted["type"], "delete_message")
                self.assertEqual(deleted["message_id"], 88)
                with plugin.storage.connect() as conn:
                    attempt = conn.execute(
                        "SELECT 1 FROM redpacket_attempt WHERE id = ?",
                        (attempt_id,),
                    ).fetchone()
                self.assertIsNone(attempt)

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
        messages = asyncio.run(
            plugin._render_redpacket_settlement(
                Context(),
                {
                    "id": "packet",
                    "status": "finished",
                    "total_amount": 40,
                    "remaining_amount": 0,
                },
            )
        )
        text = messages[0]
        self.assertIn("<b>运气王：</b>一二三四五六七八九十 · 30", text)
        self.assertIn("<b>倒霉蛋：</b>小明 · 10", text)
        self.assertIn("<details><summary>领取总名单（金额降序）</summary><ol>", text)
        self.assertNotIn("<blockquote expandable>", text)
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
        messages = asyncio.run(
            plugin._render_redpacket_settlement(
                Context(),
                {"id": "large", "status": "finished", "total_amount": 500000, "remaining_amount": 0},
            )
        )
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) < 3900 for message in messages))
        self.assertEqual(sum(message.count(" · ") for message in messages), 502)
        self.assertTrue(all("<details><summary>" in message for message in messages))
        self.assertTrue(all("<ol>" in message for message in messages))
        self.assertTrue(all("<blockquote expandable>" not in message for message in messages))

    def test_settlement_rechecks_historical_anonymous_admin_name(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Storage:
            def get_redpacket_settlement(self, account_id, redpacket_id):
                return [
                    {
                        "user_id": 77,
                        "user_display_name": "数据库中的真实姓名",
                        "reward": 30,
                        "updated_at": 2.0,
                    }
                ]

        class Client:
            async def get_permissions(self, chat_id, user_id):
                return types.SimpleNamespace(
                    anonymous=True,
                    participant=types.SimpleNamespace(rank="匿名标签"),
                )

        class Context:
            account_id = 1
            client = Client()
            config = {
                "settlement_message_template": (
                    "运气王 · {luckiest_reward}\n"
                    "倒霉蛋 · {unluckiest_reward}\n\n"
                    "{ranking}"
                )
            }

        plugin.storage = Storage()
        messages = asyncio.run(
            plugin._render_redpacket_settlement(
                Context(),
                {
                    "id": "packet",
                    "chat_id": -1001,
                    "status": "finished",
                    "total_amount": 30,
                    "remaining_amount": 0,
                },
            )
        )
        self.assertIn("运气王 · 匿名标签 · 30", messages[0])
        self.assertIn("倒霉蛋 · 匿名标签 · 30", messages[0])
        self.assertIn("1. 匿名标签 · 30", messages[0])
        self.assertNotIn("数据库中的真实姓名", messages[0])

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
        text = asyncio.run(
            plugin._render_weekly_leaderboard(
                Context(),
                -1001,
                completed=False,
                now=datetime.fromisoformat("2026-07-15T12:00:00+08:00"),
            )
        )
        self.assertIn("<details><summary>答对次数 TOP 5</summary><ol>", text)
        self.assertIn("<details><summary>获得奖金 TOP 5</summary><ol>", text)
        self.assertNotIn("<blockquote expandable>", text)
        count_section, reward_section = text.split("<summary>获得奖金 TOP 5</summary>")
        self.assertLess(count_section.index("答题王"), count_section.index("奖金王"))
        self.assertLess(reward_section.index("奖金王"), reward_section.index("答题王"))

    def test_settlement_and_weekly_templates_are_used_at_runtime(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Storage:
            def get_redpacket_settlement(self, account_id, redpacket_id):
                return []

            def weekly_leaderboard(self, account_id, chat_id, start_at, end_at):
                return []

        class Context:
            account_id = 1
            config = {
                "timezone": "Asia/Shanghai",
                "daily_limit": 3,
                "retry_count": 2,
                "command": "rain",
                "settlement_message_template": (
                    "结算 {date} {daily_limit}/{retry_count} {prefix}{command} "
                    "{redpacket_id} {status} {ranking}"
                ),
                "weekly_message_template": "周榜 {weekly_title} {period_start} {period_end} {count_ranking} / {reward_ranking}",
            }

        plugin.storage = Storage()
        with patch.object(plugin, "_today", return_value="2026-07-15"):
            settlement = asyncio.run(
                plugin._render_redpacket_settlement(
                    Context(),
                    {"id": "custom", "status": "expired", "total_amount": 100, "remaining_amount": 100},
                )
            )
        self.assertEqual(settlement, ["结算 2026-07-15 3/2 。rain custom 已到期 本次无人成功领取。"])
        weekly = asyncio.run(
            plugin._render_weekly_leaderboard(
                Context(),
                -1001,
                completed=False,
                now=datetime.fromisoformat("2026-07-15T12:00:00+08:00"),
            )
        )
        self.assertIn("周榜 AI 红包本周排行榜", weekly)
        self.assertEqual(weekly.count("本周期暂无成功答题记录。"), 2)

        with patch.object(plugin, "_today", return_value="2026-07-15"):
            fallback = plugin._render_template(Context(), "settlement_message_template", "默认 {date}", typo="x")
        self.assertIn("2026-07-15", fallback)

    def test_startup_registers_settlement_daily_reminder_and_sunday_ten_jobs(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
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
                    data_dir = Path(tempdir)
                    log = None

                await plugin.on_startup(Context())
                self.assertEqual(jobs["redpacket_settlement_scan"], {"kind": "interval", "interval_sec": 30})
                self.assertEqual(jobs["weekly_leaderboard"], {"kind": "cron", "cron": "0 10 * * 0"})
                self.assertEqual(jobs["unfinished_redpacket_reminder"], {"kind": "cron", "cron": "0 8 * * *"})
                self.assertIn("airp-7", plugin.commands)
                await plugin.on_shutdown(Context())
                self.assertEqual(unregistered, [True])

        asyncio.run(run_case())

    def test_startup_shortens_existing_packet_to_next_day_eight_thirty(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()

                class Context:
                    account_id = 1
                    config = {"timezone": "Asia/Shanghai"}
                    account_config = {}
                    scheduler = None
                    data_dir = Path(tempdir)
                    log = None

                plugin._ensure_storage(Context())
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                created_at = datetime.fromisoformat("2026-07-14T20:00:00+08:00").timestamp()
                with patch.object(storage_module.time, "time", return_value=created_at):
                    plugin.storage.create_redpacket(
                        redpacket_id="legacy-active",
                        account_id=1,
                        chat_id=-1001,
                        creator_id=1,
                        bank_id="bank",
                        total_amount=10,
                        rewards=[10],
                        ttl_seconds=200_000,
                    )

                await plugin.on_startup(Context())

                packet = plugin.storage.get_redpacket("legacy-active")
                expected = datetime.fromisoformat("2026-07-15T08:30:00+08:00").timestamp()
                self.assertEqual(packet["expires_at"], expected)

        asyncio.run(run_case())

    def test_daily_unfinished_reminder_groups_by_chat_and_publishes_once(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.AIRedpacketPlugin()
            published = set()
            queried = []
            sent = []

            class Storage:
                def unfinished_redpackets_created_between(self, account_id, start_at, end_at, now=None):
                    queried.append((account_id, start_at, end_at, now))
                    return [
                        {
                            "id": "yesterday-one",
                            "chat_id": -1001,
                            "question_count": 4,
                            "claimed_count": 1,
                            "total_amount": 100,
                            "claimed_amount": 20,
                        },
                        {
                            "id": "yesterday-two",
                            "chat_id": -1001,
                            "question_count": 2,
                            "claimed_count": 0,
                            "total_amount": 50,
                            "claimed_amount": 0,
                        },
                        {
                            "id": "another-chat",
                            "chat_id": -1002,
                            "question_count": 1,
                            "claimed_count": 0,
                            "total_amount": 10,
                            "claimed_amount": 0,
                        },
                    ]

                def daily_reminder_published(self, account_id, chat_id, packet_date):
                    return (account_id, chat_id, packet_date) in published

                def mark_daily_reminder_published(self, account_id, chat_id, packet_date):
                    published.add((account_id, chat_id, packet_date))

            class Messages:
                saved = {
                    "ai_redpacket:packet:yesterday-one": 88,
                    "ai_redpacket:packet:yesterday-two": 89,
                    "ai_redpacket:packet:another-chat": 90,
                }

                async def send(self, **kwargs):
                    sent.append(kwargs)
                    self.saved[kwargs["save_message_id_key"]] = 123

                async def send_rich(self, **kwargs):
                    sent.append(kwargs)
                    self.saved[kwargs["save_message_id_key"]] = 123

                async def read_saved_message_id(self, key):
                    return self.saved.get(key)

            class Context:
                account_id = 1
                config = {"timezone": "Asia/Shanghai"}
                messages = Messages()
                log = None

            plugin.storage = Storage()
            job = types.SimpleNamespace(fired_at=datetime.fromisoformat("2026-07-15T08:00:00+08:00"))
            await plugin._run_unfinished_redpacket_reminder(Context(), job)
            await plugin._run_unfinished_redpacket_reminder(Context(), job)

            self.assertEqual(len(sent), 2)
            chat_one = next(item for item in sent if item["chat_id"] == -1001)
            self.assertIn("昨日雨露均沾即将到期", chat_one["html"])
            self.assertIn("今日 08:30", chat_one["html"])
            self.assertIn("yesterday-one", chat_one["html"])
            self.assertIn("yesterday-two", chat_one["html"])
            self.assertIn("https://t.me/c/1/88", chat_one["html"])
            self.assertNotIn("text", chat_one)
            self.assertEqual(chat_one["save_message_id_key"], "ai_redpacket:unfinished-reminder:-1001:2026-07-14")
            self.assertEqual(
                queried[0][1:3],
                (
                    datetime.fromisoformat("2026-07-14T00:00:00+08:00").timestamp(),
                    datetime.fromisoformat("2026-07-15T00:00:00+08:00").timestamp(),
                ),
            )
            self.assertEqual(len(published), 2)

        asyncio.run(run_case())

    def test_unfinished_reminder_uses_configured_template(self) -> None:
        async def run_case() -> None:
            plugin = plugin_module.AIRedpacketPlugin()

            class Context:
                config = {
                    "reminder_message_template": "提醒 {packet_date} {expire_time}\n{redpackets}",
                }
                messages = None

            text = await plugin._render_unfinished_redpacket_reminder(
                Context(),
                -1001,
                [
                    {
                        "id": "custom-reminder",
                        "question_count": 2,
                        "claimed_count": 1,
                        "total_amount": 30,
                        "claimed_amount": 10,
                    }
                ],
                packet_date="2026-07-14",
            )

            self.assertTrue(text.startswith("提醒 2026-07-14 08:30"))
            self.assertIn("custom-reminder", text)
            self.assertIn("1/2", text)

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

                async def send_rich(self, **kwargs):
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
            self.assertIn("2026-07-12 10:00", sent[0]["html"])
            self.assertIn("2026-07-19 10:00", sent[0]["html"])
            self.assertNotIn("text", sent[0])

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
                self.assertNotIn("reply_to_message_id", actions[0])
                self.assertEqual(
                    actions[1],
                    {
                        "type": "delete_message",
                        "chat_id": -1001,
                        "message_id": 1,
                        "send_via": "userbot_reply",
                    },
                )
                self.assertEqual(actions[2], {"type": "end_session"})
                packet = plugin.storage.list_redpackets(1, -1001)[0]
                self.assertEqual(packet["total_amount"], 150000)
                self.assertEqual(packet["question_count"], 40)
                self.assertEqual(packet["bank_id"], "default-bank")

        asyncio.run(run_case())

    def test_list_command_deletes_only_command_and_keeps_active_progress_with_opening_link(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions(3))
                plugin.storage.create_redpacket(
                    redpacket_id="active123456",
                    account_id=1,
                    chat_id=-1002090852236,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=20,
                    rewards=[10, 10],
                    ttl_seconds=3600,
                )
                attempt = plugin.storage.reserve_question(
                    attempt_id="list-attempt",
                    account_id=1,
                    user_id=77,
                    chat_id=-1002090852236,
                    redpacket_id="active123456",
                    date="2026-07-14",
                    submission_token="list-token",
                    reservation_seconds=300,
                )
                plugin.storage.submit_answer(
                    attempt_id="list-attempt",
                    account_id=1,
                    user_id=77,
                    chat_id=-1002090852236,
                    redpacket_id="active123456",
                    option_index=int(attempt["answer_index"]),
                    submission_token="list-token",
                    submission_key="list-submit",
                    retry_count=1,
                )
                plugin.storage.create_redpacket(
                    redpacket_id="closed123456",
                    account_id=1,
                    chat_id=-1002090852236,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )
                plugin.storage.close_redpacket(1, -1002090852236, "closed123456")

                class Messages:
                    async def read_saved_message_id(self, key):
                        return 1017939 if key == "ai_redpacket:packet:active123456" else None

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    messages = Messages()
                    log = None

                actions = await plugin._handle_admin_command(
                    Context(), -1002090852236, 1, 999, ["list"]
                )

                self.assertEqual([action["type"] for action in actions], ["send_message", "delete_message"])
                self.assertEqual(actions[0]["send_via"], "interaction_bot")
                self.assertNotIn("reply_to_message_id", actions[0])
                self.assertIn("active123456", actions[0]["text"])
                self.assertNotIn("closed123456", actions[0]["text"])
                self.assertIn("1/2", actions[0]["text"])
                self.assertIn("10/20", actions[0]["text"])
                self.assertIn("https://t.me/c/2090852236/1017939", actions[0]["text"])
                self.assertIn("点击此处跳转，领取今日份雨露均沾", actions[0]["text"])
                self.assertIn("红包开头：<b>今日份雨露均沾 - 第 ", actions[0]["text"])
                self.assertEqual(
                    actions[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
                    "airp:repayme:active123456",
                )
                self.assertIn(
                    "-雨露均沾 · 申请补发奖励",
                    actions[0]["reply_markup"]["inline_keyboard"][0][0]["text"],
                )
                self.assertEqual(actions[1]["message_id"], 999)

        asyncio.run(run_case())

    def test_public_slash_list_is_read_only_and_does_not_delete_member_message(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="public-list",
                    account_id=1,
                    chat_id=-1002090852236,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )

                class Messages:
                    async def read_saved_message_id(self, key):
                        return 456

                class Context:
                    account_id = 1
                    config = {"timezone": "Asia/Shanghai"}
                    account_config = {}
                    messages = Messages()
                    log = None

                payload = {
                    "event_type": "message",
                    "message": {
                        "chat_id": -1002090852236,
                        "message_id": 123,
                        "text": "/airp list",
                    },
                    "sender": {"user_id": 999, "display_name": "群员"},
                }
                actions = await plugin.on_interaction(Context(), "ai_redpacket_claim", payload)

                self.assertEqual([action["type"] for action in actions], ["send_message"])
                self.assertEqual(actions[0]["send_via"], "interaction_bot")
                self.assertIn("public-list", actions[0]["text"])
                self.assertNotIn("reply_to_message_id", actions[0])
                self.assertEqual(
                    await plugin.on_interaction(
                        Context(),
                        "ai_redpacket_claim",
                        {**payload, "message": {**payload["message"], "text": "/airp reset all"}},
                    ),
                    [],
                )

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
                self.assertEqual(actions[0]["send_via"], "interaction_bot")
                self.assertIn("save_message_id_key", actions[0])
                self.assertNotIn("reply_to_message_id", actions[0])
                self.assertEqual(actions[1]["type"], "delete_message")
                self.assertEqual(actions[1]["message_id"], 1)
                with plugin.storage.connect() as conn:
                    row = conn.execute(
                        "SELECT reset_at FROM redpacket_limit_reset WHERE account_id = ? AND chat_id = ? AND user_id = ? AND date = ?",
                        (1, -1001, 77, "2026-07-14"),
                    ).fetchone()
                self.assertIsNotNone(row)

        asyncio.run(run_case())

    def test_admin_reset_all_command_resets_every_daily_participant(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                applied = []
                delays = []

                async def fake_sleep(delay):
                    delays.append(delay)

                class Messages:
                    async def read_saved_message_id(self, key):
                        return 99

                    async def apply(self, actions, **kwargs):
                        applied.append((actions, kwargs))

                class Context:
                    account_id = 1
                    config = {"timezone": "Asia/Shanghai"}
                    account_config = {}
                    messages = Messages()
                    log = None

                plugin._today = lambda ctx: "2026-07-14"
                with (
                    patch.object(
                        plugin.storage,
                        "reset_all_daily_limits",
                        return_value={"user_count": 3},
                    ) as reset_all,
                    patch.object(plugin_module.asyncio, "sleep", fake_sleep),
                ):
                    actions = await plugin._handle_admin_command(Context(), -1001, 77, 1, ["reset", "all"])
                    timer_task = next(iter(plugin._timer_tasks.values()))
                    await timer_task

                reset_all.assert_called_once_with(1, -1001, "2026-07-14")
                self.assertIn("本群 <code>2026-07-14</code> 当日全部 <code>3</code> 名用户", actions[0]["text"])
                self.assertEqual(actions[0]["send_via"], "interaction_bot")
                self.assertIn("save_message_id_key", actions[0])
                self.assertEqual(actions[1]["type"], "delete_message")
                self.assertEqual(actions[1]["send_via"], "userbot_reply")
                self.assertEqual(delays, [3])
                self.assertEqual(applied[0][0][0]["type"], "delete_message")
                self.assertEqual(applied[0][0][0]["message_id"], 99)
                self.assertEqual(applied[0][0][0]["send_via"], "interaction_bot")

                invalid = await plugin._handle_admin_command(Context(), -1001, 77, 2, ["reset", "not-a-user"])
                self.assertEqual([action["type"] for action in invalid], ["send_message"])
                self.assertEqual(invalid[0]["reply_to_message_id"], 2)

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
                self.assertEqual(
                    action_result["config_patch"]["question_bank_options"],
                    [
                        {
                            "value": action_result["config_patch"]["question_bank_id"],
                            "label": "测试题库（100 题）",
                        }
                    ],
                )
                self.assertEqual(ai.calls, 1)
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
                self.assertEqual(ai.calls, 1)
                self.assertEqual(ctx.http.calls, 1)

                create_actions = await plugin._create_packet(ctx, -1001, 99, 12, ["30", "3"])
                self.assertEqual(ai.calls, 1)
                announcement = create_actions[0]
                self.assertEqual(announcement["type"], "send_rich_message")
                self.assertIn("<h1>AI 答题红包</h1>", _action_html(announcement))
                self.assertNotIn("text", announcement)
                self.assertEqual(announcement["send_via"], "interaction_bot")
                self.assertEqual(announcement["reply_markup"]["inline_keyboard"][0][0]["text"], "领取我的雨露")
                self.assertTrue(announcement["pin"])
                self.assertTrue(announcement["save_message_id_key"].startswith("ai_redpacket:packet:"))

                ctx.config["pin_packet_message"] = False
                unpinned_actions = await plugin._create_packet(ctx, -1001, 99, 13, ["30", "3"])
                self.assertNotIn("pin", unpinned_actions[0])

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
                self.assertEqual(ctx.ai.calls, 2)
                self.assertTrue(all(call["max_tokens"] <= 65536 for call in ctx.ai.kwargs))
                self.assertTrue(any(call["max_tokens"] > 4096 for call in ctx.ai.kwargs))
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
                        "generation_count": 500,
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
                self.assertEqual(result["config_patch"]["question_bank_count"], 500)
                self.assertEqual(ctx.ai.max_active, 3)
                self.assertEqual(ctx.ai.calls, 3)

        asyncio.run(run_case())

    def test_large_single_batch_keeps_filling_when_model_returns_partial_valid_results(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")

                class HTTP:
                    async def get(self, url):
                        return types.SimpleNamespace(status_code=200, text="分次补齐使用的有效网页正文。" * 1000)

                class AI:
                    def __init__(self):
                        self.calls = 0
                        self.kwargs = []

                    async def complete(self, **kwargs):
                        self.kwargs.append(kwargs)
                        start = self.calls * 50
                        self.calls += 1
                        return types.SimpleNamespace(
                            text=json.dumps(
                                {"title": "分次补齐题库", "questions": _questions(50, start=start)},
                                ensure_ascii=False,
                            )
                        )

                class Context:
                    account_id = 1
                    config = {
                        "question_source_url": "https://example.com/partial",
                        "generation_concurrency": 1,
                    }
                    account_config = {}
                    log = None
                    http = HTTP()
                    ai = AI()

                result = await plugin._generate_bank(
                    Context(),
                    Context.config["question_source_url"],
                    target_count=200,
                )

                self.assertEqual(result["question_count"], 200)
                self.assertEqual(Context.ai.calls, 4)
                self.assertIn("已有题目题干如下", Context.ai.kwargs[1]["user"])
                self.assertIn("问题 0", Context.ai.kwargs[1]["user"])

        asyncio.run(run_case())

    def test_question_generation_streams_progress_and_does_not_call_complete(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                logs = []

                class HTTP:
                    async def get(self, url):
                        return types.SimpleNamespace(status_code=200, text="流式生成网页正文。" * 200)

                class AI:
                    async def stream_complete(self, **kwargs):
                        text = json.dumps(
                            {"title": "流式题库", "questions": _questions(3)},
                            ensure_ascii=False,
                        )
                        for start in range(0, len(text), 300):
                            yield text[start : start + 300]

                    async def complete(self, **kwargs):
                        raise AssertionError("支持流式时不应调用 complete")

                async def log(level, message, **detail):
                    logs.append((level, message, detail))

                class Context:
                    account_id = 1
                    config = {"question_source_url": "https://example.com/stream", "generation_concurrency": 1}
                    account_config = {}
                    http = HTTP()
                    ai = AI()

                ctx = Context()
                ctx.log = log
                result = await plugin._generate_bank(ctx, ctx.config["question_source_url"], target_count=3)
                self.assertEqual(result["question_count"], 3)
                self.assertTrue(any(message == "AI 题库流式生成中" for _, message, _ in logs))
                self.assertTrue(any(detail.get("实时片段") for _, message, detail in logs if message == "AI 题库流式生成中"))

        asyncio.run(run_case())

    def test_cancelled_generation_keeps_last_completed_batch_for_resume(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")

                class HTTP:
                    async def get(self, url):
                        return types.SimpleNamespace(status_code=200, text="可中断生成网页正文。" * 500)

                class AI:
                    def __init__(self):
                        self.calls = 0
                        self.second_started = asyncio.Event()

                    async def stream_complete(self, **kwargs):
                        self.calls += 1
                        if self.calls == 1:
                            yield json.dumps(
                                {"title": "阶段题库", "questions": _questions(plugin_module.GENERATION_BATCH_SIZE)},
                                ensure_ascii=False,
                            )
                            return
                        self.second_started.set()
                        await asyncio.Event().wait()
                        yield ""

                    async def complete(self, **kwargs):
                        raise AssertionError("取消测试不应回退普通生成")

                ai = AI()

                class Context:
                    account_id = 1
                    config = {"question_source_url": "https://example.com/resume", "generation_concurrency": 2}
                    account_config = {}
                    log = None
                    http = HTTP()

                ctx = Context()
                ctx.ai = ai
                task = asyncio.create_task(
                    plugin._generate_bank(
                        ctx,
                        ctx.config["question_source_url"],
                        target_count=plugin_module.GENERATION_BATCH_SIZE * 2,
                    )
                )
                await ai.second_started.wait()
                bank_id = hashlib.sha256(ctx.config["question_source_url"].encode()).hexdigest()[:12]
                for _ in range(100):
                    if len(plugin.storage.get_bank_questions(1, bank_id)) == plugin_module.GENERATION_BATCH_SIZE:
                        break
                    await asyncio.sleep(0)
                self.assertEqual(
                    len(plugin.storage.get_bank_questions(1, bank_id)),
                    plugin_module.GENERATION_BATCH_SIZE,
                )
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(
                    len(plugin.storage.get_bank_questions(1, bank_id)),
                    plugin_module.GENERATION_BATCH_SIZE,
                )

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
                self.assertEqual(ctx.ai.calls, 1)
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
                self.assertEqual(len(ai.kwargs), 1)
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

                created_at = datetime.fromisoformat("2026-07-14T20:00:00+08:00").timestamp()
                with patch.object(plugin_module.time, "time", return_value=created_at):
                    actions = await plugin._create_packet(Context(), -1001, 99, 1, ["10"])
                self.assertEqual(actions[0]["send_via"], "interaction_bot")
                self.assertEqual(actions[0]["type"], "send_message")
                self.assertEqual(actions[0]["text"], "日期 2026-07-14 限制 3/2")
                packets = plugin.storage.list_redpackets(1, -1001)
                self.assertEqual(packets[0]["bank_id"], "default-bank")
                self.assertEqual(
                    packets[0]["expires_at"],
                    datetime.fromisoformat("2026-07-15T08:30:00+08:00").timestamp(),
                )

        asyncio.run(run_case())

    def test_limit_placeholders_render_in_question_and_result_templates(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()
        plugin._today = lambda ctx: "2026-07-14"

        class Context:
            config = {
                "daily_limit": 3,
                "retry_count": 2,
                "question_message_template": "题面 {answerer_name} {date} {daily_limit}/{retry_count} {question} {options}",
                "success_message_template": "成功 {answerer_name} {date} {daily_limit}/{retry_count} {question} {reward}",
                "failed_message_template": "失败 {answerer_name} {date} {daily_limit}/{retry_count} {question} {answer}",
            }

        payload = {
            "question": "测试题",
            "source_options_json": json.dumps(["正确", "错误一", "错误二"]),
            "option_order_json": json.dumps([0, 1, 2]),
            "answer_index": 0,
            "reward": 10,
            "explanation": "解析",
            "source": "https://example.com/source",
            "user_id": 77,
            "user_display_name": "张三",
        }
        self.assertIn("题面 张三 2026-07-14 3/2", plugin._render_question(Context(), payload))
        success = plugin._render_result(Context(), payload, correct=True)
        failed = plugin._render_result(Context(), payload, correct=False)
        self.assertIn("成功 张三 2026-07-14 3/2", success)
        self.assertNotIn("申请补发奖励", success)
        self.assertNotIn("若未收到奖励", success)
        self.assertIn("失败 张三 2026-07-14 3/2", failed)

    def test_custom_templates_do_not_receive_hardcoded_answerer_labels(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Context:
            config = {
                "question_message_template": "只显示题目：{question}\n{options}",
                "success_message_template": "只显示奖励：{reward}",
                "failed_message_template": "只显示答案：{answer}",
            }

        payload = {
            "question": "测试题",
            "source_options_json": json.dumps(["正确", "错误一", "错误二"]),
            "option_order_json": json.dumps([0, 1, 2]),
            "answer_index": 0,
            "reward": 10,
            "explanation": "解析",
            "source": "https://example.com/source",
            "user_id": 77,
            "user_display_name": "张三",
        }

        self.assertNotIn("张三", plugin._render_question(Context(), payload))
        self.assertNotIn("张三", plugin._render_result(Context(), payload, correct=True))
        self.assertNotIn("张三", plugin._render_result(Context(), payload, correct=False))

    def test_saved_previous_rich_defaults_migrate_to_answerer_placeholder(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()
        payload = {
            "question": "测试题",
            "source_options_json": json.dumps(["正确", "错误一", "错误二"]),
            "option_order_json": json.dumps([0, 1, 2]),
            "answer_index": 0,
            "reward": 10,
            "explanation": "解析",
            "source": "https://example.com/source",
            "user_id": 77,
            "user_display_name": "张三",
        }

        for key, previous_default in plugin_module.PREVIOUS_RICH_TEMPLATE_DEFAULTS.items():
            class Context:
                config = {key: previous_default}

            rendered = (
                plugin._render_question(Context(), payload)
                if key == "question_message_template"
                else plugin._render_result(
                    Context(),
                    payload,
                    correct=key == "success_message_template",
                )
            )
            self.assertIn("张三", rendered)

    def test_question_and_result_messages_keep_public_claim_button_and_owner_label(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            plugin = plugin_module.AIRedpacketPlugin()
            plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
            plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
            plugin.storage.create_redpacket(
                redpacket_id="public-entry",
                account_id=1,
                chat_id=-1001,
                creator_id=1,
                bank_id="bank",
                total_amount=10,
                rewards=[10],
                ttl_seconds=3600,
            )

            class Context:
                account_id = 1
                config = {}

            actions = plugin._start_attempt(Context(), "callback", -1001, 77, "@alice", "public-entry")
            question = actions[1]
            self.assertEqual(question["type"], "send_rich_message")
            self.assertTrue(_action_html(question).startswith("<p><b>@alice 这是你的专属雨露</b></p>"))
            self.assertIn('<ol type="A">', _action_html(question))
            self.assertEqual(_action_html(question).count("<li>"), 3)
            self.assertEqual(question["reply_markup"]["inline_keyboard"][1][0]["text"], "我也要雨露均沾")
            answer_buttons = question["reply_markup"]["inline_keyboard"][0]
            self.assertEqual([button["text"] for button in answer_buttons], ["A", "B", "C"])
            self.assertTrue(all(button["callback_data"].startswith("airp:answer:public-entry:") for button in answer_buttons))
            self.assertEqual(
                question["reply_markup"]["inline_keyboard"][1][0]["callback_data"],
                "airp:start:public-entry",
            )
            repeated = plugin._start_attempt(Context(), "callback-2", -1001, 77, "@alice", "public-entry")
            self.assertEqual([action["type"] for action in repeated], ["answer_callback"])
            self.assertTrue(repeated[0]["show_alert"])
            self.assertIn("已有一道进行中", repeated[0]["text"])
            with plugin.storage.connect() as conn:
                attempt_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM redpacket_attempt WHERE user_id = ?",
                    (77,),
                ).fetchone()["count"]
            self.assertEqual(attempt_count, 1)

    def test_failed_result_uses_rich_edit_and_keeps_join_button(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="failed-rich",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )
                attempt = plugin.storage.reserve_question(
                    attempt_id="failed-attempt",
                    account_id=1,
                    user_id=77,
                    chat_id=-1001,
                    redpacket_id="failed-rich",
                    date="2026-07-18",
                    submission_token="failed-token",
                    reservation_seconds=300,
                    retry_count=0,
                    user_display_name="张三",
                )

                class Context:
                    account_id = 1
                    config = {"retry_count": 0}
                    account_config = {}
                    scheduler = None
                    log = None

                wrong_index = (int(attempt["answer_index"]) + 1) % 3
                actions = await plugin._submit_attempt(
                    Context(),
                    {"message": {"chat_id": -1001, "message_id": 88}},
                    "failed-callback",
                    -1001,
                    77,
                    "failed-rich",
                    "failed-attempt",
                    wrong_index,
                    "failed-token",
                )

                self.assertEqual(actions[0]["type"], "answer_callback")
                edit = next(action for action in actions if action["type"] == "edit_message")
                self.assertEqual(edit["send_via"], "interaction_bot")
                self.assertIn("<details open>", _action_html(edit))
                self.assertIn("今天的挑战已结束", _action_html(edit))
                self.assertEqual(edit["reply_markup"]["inline_keyboard"][0][0]["text"], "我也要雨露均沾")
                self.assertEqual(
                    edit["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
                    "airp:start:failed-rich",
                )

        asyncio.run(run_case())

    def test_saved_custom_telegram_html_templates_stay_on_plain_message_path(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="legacy-custom",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )

                class Context:
                    account_id = 1
                    config = {
                        "question_message_template": "<b>旧题面</b>\n{question}\n\n{options}",
                        "success_message_template": "<b>旧结果</b>\n{question}\n奖励 {reward}",
                        "weekly_message_template": (
                            "<b>{weekly_title}</b>\n<blockquote expandable>{count_ranking}\n{reward_ranking}</blockquote>"
                        ),
                    }
                    account_config = {}
                    log = None

                actions = plugin._start_attempt(Context(), "legacy-callback", -1001, 77, "张三", "legacy-custom")
                question = actions[1]
                self.assertEqual(question["type"], "send_message")
                self.assertEqual(question["parse_mode"], "html")
                self.assertNotIn("rich_message", question)
                self.assertIn("<b>旧题面</b>\n", question["text"])
                self.assertIn("A. ", question["text"])
                answer_callback = question["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
                self.assertEqual(answer_callback.split(":")[:3], ["airp", "answer", "legacy-custom"])

                attempt_id = answer_callback.split(":")[3]
                with plugin.storage.connect() as conn:
                    attempt = conn.execute(
                        "SELECT answer_index, submission_token FROM redpacket_attempt WHERE id = ?",
                        (attempt_id,),
                    ).fetchone()
                result_actions = await plugin._submit_attempt(
                    Context(),
                    {"message": {"chat_id": -1001, "message_id": 88}},
                    "legacy-success",
                    -1001,
                    77,
                    "legacy-custom",
                    attempt_id,
                    int(attempt["answer_index"]),
                    str(attempt["submission_token"]),
                )
                edit = next(action for action in result_actions if action["type"] == "edit_message")
                self.assertEqual(edit["parse_mode"], "html")
                self.assertNotIn("rich_message", edit)
                self.assertIn("<b>旧结果</b>\n", edit["text"])

                self.assertFalse(
                    plugin._uses_rich_template(
                        Context(),
                        "weekly_message_template",
                        plugin_module.WEEKLY_MESSAGE_TEMPLATE,
                        plugin_module.LEGACY_WEEKLY_MESSAGE_TEMPLATE,
                    )
                )

        asyncio.run(run_case())

    def test_saved_legacy_builtin_template_is_migrated_to_rich_message(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Context:
            config = {"packet_message_template": plugin_module.LEGACY_PACKET_MESSAGE_TEMPLATE}

        self.assertTrue(
            plugin._uses_rich_template(
                Context(),
                "packet_message_template",
                plugin_module.PACKET_MESSAGE_TEMPLATE,
                plugin_module.LEGACY_PACKET_MESSAGE_TEMPLATE,
            )
        )

        class RichCustomContext:
            config = {
                "packet_message_template": "<h1>自定义开场</h1><p>红包 {redpacket_id}</p>"
            }

        self.assertTrue(
            plugin._uses_rich_template(
                RichCustomContext(),
                "packet_message_template",
                plugin_module.PACKET_MESSAGE_TEMPLATE,
                plugin_module.LEGACY_PACKET_MESSAGE_TEMPLATE,
            )
        )

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

                class Client:
                    async def get_permissions(self, chat_id, user_id):
                        return types.SimpleNamespace(
                            anonymous=False,
                            participant=types.SimpleNamespace(rank=""),
                        )

                    def iter_messages(self, chat_id, *, from_user=None, limit=None):
                        async def stream():
                            yield types.SimpleNamespace(id=777, sender_id=77)

                        return stream()

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    client = Client()
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
                    user_display_name="张三",
                )

                class Client:
                    async def get_permissions(self, chat_id, user_id):
                        return types.SimpleNamespace(
                            anonymous=False,
                            participant=types.SimpleNamespace(rank=""),
                        )

                    def iter_messages(self, chat_id, *, from_user=None, limit=None):
                        async def stream():
                            yield types.SimpleNamespace(id=777, sender_id=77)

                        return stream()

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    client = Client()
                    log = None

                payload = {
                    "source": {"type": "callback_query"},
                    "message": {"chat_id": -1001, "message_id": 88},
                    "sender": {"user_id": 77},
                }
                premature = await plugin._handle_callback(
                    Context(), payload, "premature-retry", "airp:repay:rp1:a1"
                )
                self.assertEqual(premature[0]["text"], "这道题还没有可申请补发的奖励")
                self.assertEqual([action["type"] for action in premature], ["answer_callback"])

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
                edit = next(action for action in actions if action["type"] == "edit_message")
                self.assertEqual(edit["send_via"], "interaction_bot")
                self.assertIn("rich_message", edit)
                self.assertIn("<details open>", _action_html(edit))
                buttons = edit["reply_markup"]["inline_keyboard"]
                self.assertEqual(buttons[0][0]["text"], "2026-07-13-雨露均沾 · 申请补发奖励")
                self.assertEqual(buttons[0][0]["callback_data"], "airp:repay:rp1:a1")
                self.assertEqual(buttons[1][0]["text"], "我也要雨露均沾")
                self.assertIn("答题者：张三", edit["text"])
                self.assertNotIn("若未收到奖励", edit["text"])
                self.assertNotIn("已发放的奖励不会重复发放", edit["text"])
                self.assertEqual(payout["amount"], 10)
                self.assertIsInstance(payout["amount"], int)
                self.assertEqual(payout["payout_key"], "ai_redpacket:1:rp1:1:77")
                self.assertEqual(payout["reply_to_user_id"], 77)
                self.assertEqual(payout["parse_mode"], "html")
                self.assertIn("暂未找到 张三 在本群的近期发言", payout["reply_anchor_missing_text"])
                self.assertIn("请先在群里任意发言一次", payout["reply_anchor_missing_text"])
                self.assertIn("也可发送以下命令触发补发按钮", payout["reply_anchor_missing_text"])
                self.assertIn("<code>/airp list</code>", payout["reply_anchor_missing_text"])
                self.assertNotIn("用户（77）", payout["reply_anchor_missing_text"])

                retry_payload = {
                    "source": {"type": "callback_query"},
                    "message": {"chat_id": -1001, "message_id": 88},
                    "sender": {"user_id": 77, "display_name": "张三"},
                }
                retry_actions = await plugin._handle_callback(
                    Context(), retry_payload, "retry-payout", "airp:repay:rp1:a1"
                )
                self.assertIn("已找到你的近期发言", retry_actions[0]["text"])
                self.assertIn("+10", retry_actions[0]["text"])
                retry_payout = next(action for action in retry_actions if action["type"] == "payout")
                self.assertEqual(retry_payout["payout_key"], payout["payout_key"])
                self.assertEqual(retry_payout["amount"], 10)
                self.assertEqual(retry_payout["reply_to_message_id"], 777)
                self.assertEqual(plugin.storage.get_redpacket("rp1")["remaining_amount"], 0)

                center_actions = await plugin._handle_callback(
                    Context(), retry_payload, "center-retry", "airp:repayme:rp1"
                )
                center_payout = next(action for action in center_actions if action["type"] == "payout")
                self.assertEqual(center_payout["payout_key"], payout["payout_key"])

                other_payload = {
                    "source": {"type": "callback_query"},
                    "message": {"chat_id": -1001, "message_id": 88},
                    "sender": {"user_id": 999, "display_name": "路人"},
                }
                denied = await plugin._handle_callback(
                    Context(), other_payload, "other-retry", "airp:repay:rp1:a1"
                )
                self.assertEqual(denied[0]["text"], "点点点，不是你的奖励你也点！")
                self.assertTrue(denied[0]["show_alert"])

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

    def test_payout_missing_anchor_notice_truncates_user_name(self) -> None:
        plugin = plugin_module.AIRedpacketPlugin()

        class Context:
            account_id = 1

        action = plugin._payout_action(
            Context(),
            -1001,
            77,
            "rp1",
            {
                "reward": 10,
                "question_slot_id": 1,
                "user_display_name": "一二三四五六七八九十十一十二",
            },
        )

        self.assertIn("暂未找到 一二三四五六七八九十 在本群的近期发言", action["reply_anchor_missing_text"])
        self.assertEqual(action["reply_to_display_name"], "一二三四五六七八九十")
        self.assertIsNone(action["reply_to_username"])
        self.assertNotIn("十一十二", action["reply_anchor_missing_text"])
        self.assertNotIn("77", action["reply_anchor_missing_text"])

    def test_payout_retry_without_recent_message_only_shows_alert(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")
                plugin.storage.replace_bank(account_id=1, bank_id="bank", title="测试", questions=_questions())
                plugin.storage.create_redpacket(
                    redpacket_id="no-speech",
                    account_id=1,
                    chat_id=-1001,
                    creator_id=1,
                    bank_id="bank",
                    total_amount=10,
                    rewards=[10],
                    ttl_seconds=3600,
                )
                attempt = plugin.storage.reserve_question(
                    attempt_id="no-speech-attempt",
                    account_id=1,
                    user_id=77,
                    chat_id=-1001,
                    redpacket_id="no-speech",
                    date="2026-07-14",
                    submission_token="token",
                    reservation_seconds=300,
                )
                plugin.storage.submit_answer(
                    attempt_id="no-speech-attempt",
                    account_id=1,
                    user_id=77,
                    chat_id=-1001,
                    redpacket_id="no-speech",
                    option_index=int(attempt["answer_index"]),
                    submission_token="token",
                    submission_key="submit",
                    retry_count=1,
                )

                class EmptyClient:
                    def iter_messages(self, chat_id, **kwargs):
                        async def stream():
                            if False:
                                yield None

                        return stream()

                class Context:
                    account_id = 1
                    config = {}
                    account_config = {}
                    client = EmptyClient()
                    log = None

                actions = await plugin._request_payout_retry(
                    Context(),
                    callback_id="retry",
                    chat_id=-1001,
                    user_id=77,
                    redpacket_id="no-speech",
                    attempt_id="no-speech-attempt",
                )

                self.assertEqual([action["type"] for action in actions], ["answer_callback"])
                self.assertTrue(actions[0]["show_alert"])
                self.assertIn("以个人账号", actions[0]["text"])
                self.assertIn("匿名管理员或频道身份", actions[0]["text"])

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
