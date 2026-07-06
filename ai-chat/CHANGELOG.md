# 更新日志

## 0.1.0 (2026-07-06)
- 新增 `AI-Chat` 远程插件（key: `ai-chat`），按 TelePilot 标准插件结构提供 `plugin.json`、`manifest.py`、`plugin.py` 与 `__init__.py`。
- 通过 TelePilot `ctx.ai.complete` 调用已配置的 AI Provider，支持 `{prefix}ask 文本`、回复消息后解释内容、查看 Provider 与清空当前会话记忆。
- 支持私聊直接陪聊、群聊 @当前账号或回复当前账号消息触发，并提供会话白名单、群组限制、短期上下文记忆和输出截断配置。
