from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, account_id=1, feature_key="quick_qa", log=None, config=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.log = log
            self.config = config or {}
            self.messages = None
            self.http = None
            self.ai = None

    def register(cls):
        return cls

    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module

    spec = importlib.util.spec_from_file_location(
        "quick_qa_plugin_under_test",
        ROOT / "quick_qa" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, PluginContext


plugin_module, PluginContext = _load_plugin_module()


def payment_payload(user_id: int, name: str, amount: int = 100) -> dict:
    return {
        "event": {"type": "payment_confirmed", "chat_id": -100123, "message_id": user_id},
        "source": {"type": "payment_confirmed", "chat_id": -100123, "message_id": user_id},
        "actor": {"user_id": 999, "display_name": "通知Bot"},
        "payment": {"amount": amount, "payer": {"user_id": user_id, "display_name": name}},
        "payer_user_id": user_id,
        "payer_name": name,
        "amount": amount,
        "chat_id": -100123,
        "message_id": user_id,
    }


def callback_payload(data: str, user_id: int, name: str, message_id: int = 500) -> dict:
    return {
        "event": {
            "type": "callback_query",
            "chat_id": -100123,
            "message_id": message_id,
            "callback_query_id": f"cb-{user_id}-{message_id}",
            "callback_data": data,
        },
        "source": {
            "type": "callback_query",
            "chat_id": -100123,
            "message_id": message_id,
            "callback_query_id": f"cb-{user_id}-{message_id}",
            "callback_data": data,
        },
        "actor": {"user_id": user_id, "display_name": name},
        "callback_query_id": f"cb-{user_id}-{message_id}",
        "callback_data": data,
        "chat_id": -100123,
        "message_id": message_id,
    }


def message_payload(text: str, user_id: int, name: str, *, channel: str = "interaction_bot", message_id: int = 700) -> dict:
    return {
        "event": {"type": "message", "chat_id": -100123, "message_id": message_id},
        "source": {"type": "message", "channel": channel, "chat_id": -100123, "message_id": message_id},
        "message": {"chat_id": -100123, "message_id": message_id, "text": text},
        "actor": {"user_id": user_id, "display_name": name},
        "chat_id": -100123,
        "message_id": message_id,
    }


class QuickQATest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data_path = plugin_module.DATA_PATH
        plugin_module.DATA_PATH = Path(self._tmp.name) / "quickqa_data.json"
        self._old_choice = plugin_module.random.choice
        plugin_module.random.choice = lambda seq: list(seq)[0]

    def tearDown(self) -> None:
        plugin_module.random.choice = self._old_choice
        plugin_module.DATA_PATH = self._old_data_path
        self._tmp.cleanup()

    def _seed_kb(self) -> None:
        store = {
            "version": 1,
            "accounts": {
                "1": {
                    "drafts": {},
                    "knowledge_bases": [
                        {
                            "kb_id": "kb1",
                            "title": "测试题库",
                            "url": "https://example.com/a",
                            "summary": "测试",
                            "questions": [
                                {
                                    "question": "TelePilot 插件新主路径是什么？",
                                    "options": ["Event Bus", "直接 Bot API", "shell"],
                                    "answer_index": 0,
                                    "explanation": "新插件走 Event Bus + MessageOps。",
                                }
                            ],
                            "created_at": 1,
                        }
                    ],
                }
            },
        }
        plugin_module._save_store(store)

    def test_extract_json_and_validate_question(self) -> None:
        data = plugin_module._extract_json_object(
            "```json\n{\"questions\":[{\"question\":\"Q\",\"options\":[\"A\",\"B\",\"C\"],\"answer_index\":1}]}\n```"
        )
        question = plugin_module._question_from_dict(data["questions"][0])
        self.assertIsNotNone(question)
        self.assertEqual(question.answer_index, 1)

    def test_draft_save_persists_knowledge_base(self) -> None:
        plugin = plugin_module.QuickQAPlugin()
        draft = {
            "draft_id": "d1",
            "kb_id": "kb-draft",
            "title": "草稿题库",
            "url": "https://example.com",
            "summary": "摘要",
            "questions": [
                {"question": "Q1", "options": ["A", "B", "C"], "answer_index": 0},
                {"question": "Q2", "options": ["A", "B", "C"], "answer_index": 1},
                {"question": "Q3", "options": ["A", "B", "C"], "answer_index": 2},
            ],
        }

        plugin._put_draft(1, draft)
        text = plugin._save_draft(1, "d1")
        kbs = plugin._available_kbs_for_account(1)

        self.assertIn("题库已保存", text)
        self.assertEqual(len(kbs), 1)
        self.assertEqual(kbs[0].kb_id, "kb-draft")

    def test_config_action_generates_knowledge_base_patch(self) -> None:
        class FakeHTTP:
            async def get(self, _url: str):
                return types.SimpleNamespace(
                    status_code=200,
                    text="<html><body><h1>TelePilot</h1>" + ("插件配置框架支持题库生成。" * 30) + "</body></html>",
                )

        class FakeAI:
            def __init__(self):
                self.calls = []

            async def complete(self, *_args, **_kwargs):
                self.calls.append(_kwargs)
                return types.SimpleNamespace(
                    text=(
                        '{"title":"配置框架","summary":"通用配置页动作",'
                        '"questions":['
                        '{"question":"配置页动作由谁声明？","options":["插件","数据库","主题"],"answer_index":0},'
                        '{"question":"题库来源是什么？","options":["URL","贴纸","头像"],"answer_index":0},'
                        '{"question":"答案按钮数量？","options":["三个","一个","五个"],"answer_index":0}'
                        ']}'
                    )
                )

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={"allowed_source_hosts": "example.com"},
            )
            ctx.http = FakeHTTP()
            fake_ai = FakeAI()
            ctx.ai = fake_ai
            result = await plugin.on_config_action(
                ctx,
                "generate_knowledge_base",
                {
                    "input": {"url": "https://example.com/article", "title": "配置页"},
                    "config": {
                        "knowledge_bases": [
                            {
                                "kb_id": "old",
                                "title": "旧题库",
                                "enabled": False,
                                "questions": [
                                    {"question": "旧题", "options": ["A", "B", "C"], "answer_index": 0}
                                ],
                            }
                        ],
                        "ai_timeout_seconds": 90,
                    },
                },
            )
            items = result["config_patch"]["knowledge_bases"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["kb_id"], "old")
            self.assertFalse(items[0]["enabled"])
            self.assertEqual(items[1]["title"], "配置页")
            self.assertTrue(items[1]["enabled"])
            self.assertEqual(len(items[1]["questions"]), 3)
            self.assertEqual(fake_ai.calls[0]["timeout_seconds"], plugin_module.DEFAULT_AI_TIMEOUT_SECONDS)

        asyncio.run(scenario())

    def test_config_action_appends_and_deduplicates_existing_kb(self) -> None:
        class FakeHTTP:
            async def get(self, _url: str):
                return types.SimpleNamespace(
                    status_code=200,
                    text="<html><body>" + ("TelePilot 支持插件配置动作和题库生成。" * 40) + "</body></html>",
                )

        class FakeAI:
            async def complete(self, *_args, **_kwargs):
                return types.SimpleNamespace(
                    text=(
                        '{"title":"配置框架","summary":"增量题库",'
                        '"questions":['
                        '{"question":"旧题会重复吗？","options":["不会","会","不确定"],"answer_index":0},'
                        '{"question":"新增题来自哪里？","options":["URL正文","骰子","头像"],"answer_index":0},'
                        '{"question":"每题几个选项？","options":["三个","两个","四个"],"answer_index":0}'
                        ']}'
                    )
                )

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={"allowed_source_hosts": "example.com", "ai_question_count": 10},
            )
            ctx.http = FakeHTTP()
            ctx.ai = FakeAI()
            result = await plugin.on_config_action(
                ctx,
                "generate_knowledge_base",
                {
                    "input": {"url": "https://example.com/article", "mode": "append", "target_total": 5},
                    "config": {
                        "knowledge_bases": [
                            {
                                "kb_id": "kb-existing",
                                "title": "配置框架",
                                "url": "https://example.com/article/",
                                "enabled": True,
                                "questions": [
                                    {"question": "旧题会重复吗？", "options": ["不会", "会", "不确定"], "answer_index": 0}
                                ],
                            }
                        ],
                    },
                },
            )
            items = result["config_patch"]["knowledge_bases"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["kb_id"], "kb-existing")
            self.assertEqual(len(items[0]["questions"]), 3)
            self.assertEqual(
                [item["question"] for item in items[0]["questions"]],
                ["旧题会重复吗？", "新增题来自哪里？", "每题几个选项？"],
            )

        asyncio.run(scenario())

    def test_interaction_bot_command_echo_is_ignored(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(account_id=1, config={"command": "qa"})
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("qa 100 20", 111, "管理员"),
                )

                self.assertEqual(actions, [])
                self.assertNotIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_route_creates_lobby(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(account_id=1, config={"command": "qa", "entry_fee": 100, "start_keyword": "我要答题"})
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("我要答题", 111, "玩家A"),
                )

                self.assertIn(-100123, plugin._games)
                self.assertTrue(any("快问快答报名中" in action.get("text", "") for action in actions))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_transfer_command_message_does_not_reopen_lobby(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(account_id=1, config={"command": "qa", "entry_fee": 100, "start_keyword": "我要答题"})
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("我要答题", 111, "玩家A"),
                )

                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("+100", 111, "玩家A", message_id=701),
                )

                self.assertEqual(actions, [])
                self.assertIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_payment_without_lobby_is_ignored(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(account_id=1)
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(ctx, "join_quick_qa", payment_payload(111, "玩家A"))

                self.assertEqual(actions, [])
                self.assertNotIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_paid_game_selects_kb_and_settles_after_answer(self) -> None:
        self._seed_kb()

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={
                    "min_players": 2,
                    "question_timeout_seconds": 300,
                    "selection_timeout_seconds": 300,
                    "max_questions_per_game": 1,
                },
            )
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(ctx, "join_quick_qa", message_payload("开始答题", 999, "主持人"))
                await plugin.on_interaction(ctx, "join_quick_qa", payment_payload(111, "玩家A"))
                await plugin.on_interaction(ctx, "join_quick_qa", payment_payload(222, "玩家B"))
                game = plugin._games[-100123]
                self.assertEqual(len(game.players), 2)

                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:start:{game.game_id}", 111, "玩家A"),
                )
                self.assertTrue(any("题库选择" in action.get("text", "") for action in actions))
                self.assertEqual(game.selector_user_id, 111)

                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:go:{game.game_id}", 111, "玩家A"),
                )
                self.assertTrue(any("TelePilot 插件新主路径" in action.get("text", "") for action in actions))

                question_id = game.current_question.question_id
                wrong = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:ans:{game.game_id}:{question_id}:1", 222, "玩家B", message_id=600),
                )
                self.assertEqual(game.players[222].points, 15)
                self.assertFalse(any(action.get("type") == "result" for action in wrong))

                right = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:ans:{game.game_id}:{question_id}:0", 111, "玩家A", message_id=601),
                )
                result = next(action for action in right if action.get("type") == "result")
                self.assertEqual(result["settlement"]["winner_user_id"], 111)
                self.assertEqual(result["settlement"]["amount"], 180)
                self.assertNotIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
