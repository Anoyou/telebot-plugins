from __future__ import annotations

import asyncio
import importlib
import json
import random
import sys
import tempfile
import types
import unittest
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

    def test_single_slot_cannot_be_reserved_concurrently(self) -> None:
        self._create_packet(count=1)
        self._reserve(201)
        with self.assertRaisesRegex(storage_module.StorageError, "暂时没有可领取"):
            self._reserve(202)

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

    def test_config_action_generates_bank_once_and_create_reuses_it(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tempdir:
                plugin = plugin_module.AIRedpacketPlugin()
                plugin.storage = storage_module.AIStorage(Path(tempdir) / "db.sqlite3")

                class HTTP:
                    async def get(self, url):
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
                                    "questions": _questions(3),
                                },
                                ensure_ascii=False,
                            )
                        )

                ai = AI()

                class Context:
                    account_id = 1
                    config = {
                        "question_source_url": "https://example.com/source",
                        "generation_count": 3,
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
                self.assertEqual(action_result["config_patch"]["question_bank_count"], 3)
                self.assertEqual(ai.calls, 1)
                self.assertEqual(ai.kwargs[0]["route"], "auto")
                self.assertNotIn("provider_tag", ai.kwargs[0])

                repeated = await plugin.on_config_action(
                    ctx,
                    "generate_question_bank",
                    {"config": dict(ctx.config)},
                )
                self.assertIn("无需重复生成", repeated["message"])
                self.assertEqual(ai.calls, 1)

                create_actions = await plugin._create_packet(ctx, -1001, 99, 12, ["30", "3"])
                self.assertEqual(ai.calls, 1)
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
                        "generation_count": 25,
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

                self.assertEqual(result["config_patch"]["question_bank_count"], 25)
                self.assertEqual(ctx.ai.calls, 4)
                self.assertTrue(all(call["max_tokens"] <= 4096 for call in ctx.ai.kwargs))
                self.assertTrue(all("本批只生成" in call["user"] for call in ctx.ai.kwargs))
                self.assertTrue(any(message == "AI 题库分批结果无效，准备重试" for _, message, _ in logs))
                self.assertTrue(any(message == "AI 题库生成进度" for _, message, _ in logs))
                banks = plugin.storage.list_banks(1)
                self.assertEqual(banks[0]["question_count"], 25)

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
                        self.kwargs = None

                    async def complete(self, **kwargs):
                        self.kwargs = kwargs
                        return types.SimpleNamespace(
                            text=json.dumps({"title": "固定模型题库", "questions": _questions(3)}, ensure_ascii=False)
                        )

                ai = AI()

                class Context:
                    account_id = 1
                    config = {
                        "question_source_url": "https://example.com/source",
                        "generation_count": 3,
                        "telepilot_provider": "provider-1",
                        "telepilot_model": "model-1",
                    }
                    account_config = {}
                    log = None
                    http = HTTP()

                ctx = Context()
                ctx.ai = ai
                await plugin.on_config_action(ctx, "generate_question_bank", {"config": dict(ctx.config)})
                self.assertEqual(ai.kwargs["route"], "fixed")
                self.assertEqual(ai.kwargs["provider"], "provider-1")
                self.assertEqual(ai.kwargs["model"], "model-1")
                self.assertNotIn("provider_tag", ai.kwargs)

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
