# 插件全面更新规范（2026-07-10，按最新开发指南）

本文件是本轮所有子 Agent 的**统一改动契约**。目标：按 `TelePilot/docs/PLUGIN-*.md` 最新开发指南更新插件，**绝不破坏原有插件逻辑**（游戏规则、命令行为、文案语义、玩家体验一律保持）。

## 绝对红线
1. 不改游戏/业务规则、赔率、金额语义、命令名默认值、玩家可见文案的含义。
2. 不删除任何现在能工作的代码路径；新写法与旧写法冲突时用"新为主路径、旧为 fallback"。
3. 不动 RBQ 后缀插件（`*-byRBQ`）的业务逻辑：它们是 PagerMaid 兼容 shim（`legacy_main.py` + 假 `pagermaid` runtime），只做 Tier 1 元数据/卫生更新。
4. 每个插件改完必须能编译：`python3 -c "import ast; ast.parse(open('<plugin>/plugin.py').read()); ast.parse(open('<plugin>/manifest.py').read())"`。
5. `plugin.json` 必须是合法 JSON。

## Tier 1 — 元数据同步与卫生（所有插件必做，零逻辑风险）
校验门 `scripts/validate-installed-interaction-plugins.py` 的要求，逐项对齐：
- `plugin.json.name`（或 `key`）== `MANIFEST.key` == 插件类 `key`。
- `plugin.json.version` == `MANIFEST.version`（本轮**递增 patch 版本**，两处保持相等）。
- `plugin.json.category` == `MANIFEST.category`。
- `plugin.json.interaction_profile` == `MANIFEST.interaction_profile`。
- `plugin.json.interaction_entries` == `MANIFEST.interaction_entries`（逐项相等）。
- `plugin.json.event_subscriptions` == `MANIFEST.event_subscriptions`（逐项相等）。
- `plugin.json.capabilities` == `MANIFEST.capabilities`（没有高风险能力也要显式写 `{}`）。
- `usage` 存在（顶层 `usage` 或 `config_schema["x-usage-guide"]` 之一非空）。
- `min_telepilot_version` 存在（缺失补 `"0.33.0"`，manifest 同步）。
- 全目录无废弃 token：`bbot_notice`、`notice_bot`、`raw_event`（*.py/*.json/*.md 都查）。
- `result_contract.send_via` 不含旧 notice 值（`notice`/`bbot_notice`/`notice_bot`）。
- 帮助/用法文案里写死的命令前缀（如 `,guess`）改成 `{prefix}` 模板占位（仅文案，不改解析逻辑）。

## Tier 2 — payload 主路径现代化（仅非 RBQ、且当前用旧平铺 payload 的插件）
指南禁止 #2「依赖旧平铺 payload 作为新插件主路径」。做法（**行为保留**）：
- 在 `on_interaction` 里，主路径改用 `from app.worker.plugins.events import event_from_interaction_payload` 后 `event = event_from_interaction_payload(payload)`，读 `event.type`、`event.message.text`、`event.message.chat_id`、`event.message.message_id`、`event.sender.user_id` 等标准字段。
- 现有的 `_payload_event` / `payload["text"]` / `payload["chat_id"]` 等旧 helper **保留为 fallback**（新字段取不到时回退），不要删。
- 参考实现：`reply_anchor_test/plugin.py`（黄金写法）；`TelePilot/examples/plugins/event_bus_demo/plugin.py`。
- 不确定字段映射时，保持旧路径不动，只在旧路径取不到时补 tp_event 兜底——宁可少改不可改错。

## Tier 3 — session.data 持久化（可选，仅在能确认无回归时做）
框架时序 bug 已在本分支修复（A1 + 两伴随修，session.data 跨通道可用）。指南铁律 #3 要求「不要把单局状态只放进程内 dict」。
- **仅用增量方式**：保留现有 `self._games` 等进程内状态机（保证同进程续局零回归），额外在开局/状态变更时返回 `{"type":"update_session","data":{...}}` 把关键状态镜像进 `session.data`；续会话/过期时若进程内丢失再从 `session.data` 兜底恢复。
- 若该插件是免费参与/按钮加入/纯进程内玩法且当前工作正常，评估收益后**可不做 Tier 3**，在 CHANGELOG 说明保留原状态机。
- 绝不为了 Tier 3 删掉正在工作的进程内状态机。

## CHANGELOG（每个改动的插件必写）
在该插件 `CHANGELOG.md` 顶部加一节，日期 `2026-07-10`，版本号与本轮 bump 后一致，中文，例如：
```
## <新版本> (2026-07-10)
- 按最新插件开发指南对齐：<具体做了什么，如 payload 改用标准事件信封主路径、元数据同步、{prefix} 模板化>。
- 保持原有<游戏/命令>逻辑与文案不变。
```

## 交付回报（每个 Agent 完成后返回）
逐插件报告：版本 old→new、做了哪些 Tier、是否跳过 Tier 3 及原因、编译是否通过、有无风险点。
