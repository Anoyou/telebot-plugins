# 插件库更新日志

本文件记录插件库**仓库级**的变化：配合 TelePilot 新平台能力的批量适配、仓库工具（兼容检查、市场索引）等。每个插件自己的详细变化写在插件目录下的 `CHANGELOG.md`（随插件安装，插件详情页显示）。

TelePilot 主仓库的 `CHANGELOG.md` 只写主仓库（服务器、Web、iOS）的改动；涉及插件的版本号与改动说明统一记在这里。下面「配合 TelePilot x.y.z」表示该批插件改用的新能力从哪个服务器版本开始可用；除特别说明外都**不抬高 `min_telepilot_version`**，旧服务器照旧可用。

## 2026-11-01 · 配合 TelePilot 0.115.0-beta.4（Plugin API 1.0 冻结范围已定）

- `scripts/check_host_compat.py`：宿主提供 `app.worker.plugins.stability.manifest_parity_problems`（TelePilot 0.115.0-beta.4+）时，按冻结范围补查 `manifest.py` 与 `plugin.json` 的其余稳定字段（`name/key`、`display_name`、`capabilities`、`config_actions`、`interaction_profile`、`agent_tools`）：两边都写了且不一致才报错，以 `plugin.json` 为准；`author` / `description` / `usage` 不一致只给警告。旧宿主没有这个函数，检查结论与之前完全相同。
- 现状：42 个插件补查后 0 个错误；4 个插件有说明文字不一致的警告（`codex_image`、`dead_revolver` 的 `author`，`lottery_plus`、`ten_half` 的 `description`），以 `plugin.json` 为准，不影响安装与运行，本批不改插件。
- `scripts/build_registry.py`：`registry.json` **目录版本 1 定死**——`schema_version` 必须为 1，顶层 / 条目字段、`kind`、`trust` 只能取冻结清单里的值（与主仓库 `stability.REGISTRY_V1_*` 一致），生成与 `--check` 越界即失败；新增 `tests/test_registry_v1.py`（宿主有冻结清单时还核对两边一致）。`registry.json` 内容不变。
- 本批**不改任何插件**、不抬任何 `min_telepilot_version`；TelePilot 侧 20 项兼容层全部保留。

## 2026-10-31 · 配合 TelePilot 0.114.0-beta.2（契约版本探测，Plugin API 1.7）

- `scripts/check_host_compat.py` 报告并打印宿主的 Plugin API 版本与插件稳定契约版本（JSON 报告新增 `plugin_api_version`、`contract_version`；宿主早于契约 1.0 时为 `null`）。只做展示，不影响检查结论。
- 插件作者如需在运行时读契约版本：`getattr(getattr(ctx, "capabilities", None), "contract_version", None)`，旧服务器为 `None`，回退即可；不要因此抬 `min_telepilot_version`。本批**不改任何插件**。
- TelePilot 侧兼容层（`on_interaction` 迁移桥、旧按钮 `callback_data`、兜底入口表等）按 10-31 决定全部保留，官方插件无需迁移；清单见主仓库 `docs/PLUGIN-API-1.0-PLAN.md`。

## 2026-10-30 · 插件市场索引

- 新增 `scripts/build_registry.py` 与根目录 `registry.json`（42 个插件），CI 在 PR 上校验、main 推送后自动重新生成提交；配合 TelePilot 0.113.0 插件市场（官方源读取本仓库 `registry.json`）。不改插件。

## 2026-10-30 · 配合 TelePilot 0.112.0-beta.3（`legacy_entry_keys`，Plugin API 1.6）

- `math10` 1.0.15（`start_math10`）、`dice_grid_hunt` 1.1.39（`answer_dice_grid_hunt`）、`guess_number` 1.0.22（`start_game`）在正式入口下声明自己早期用过的旧入口 key。没有写 `entry_policy`（0.112.0-beta.2 见到它会整表跳过兜底），旧宿主照旧走兜底入口表，结论相同。宿主的兜底入口表只为这些插件更早、没有声明的版本保留（计划 Plugin API 2.0 移除）。

## 2026-10-29 · 配合 TelePilot 0.112.0-beta.2（入口策略、扩展点、规则编辑器，Plugin API 1.5）

- `game24` 1.1.18、`pt_promote` 1.0.30、`blackjack` 1.0.33 声明 `entry_policy`（与原有顶层字段一致，新旧服务器结论相同）。
- `game24` 声明 `ui.rule_editor` 启动词提示。
- `codex_image` 1.2.1 声明 `provides: ["ai.image_backend"]`（`,ai` 图片模板的“图片插件（自动选择）”后端）。
- 游戏币结算与派奖逻辑无任何变化。

## 2026-10-29 · 配合 TelePilot 0.111.0-beta.1（转账通知解析扩展点 `payment_sources`）

- 新增官方插件 `payment-source-cn-transfer` 1.0.0：宿主内置转账通知正则的逐字迁移，导出 `parse_payment_notice(text, meta)`；宿主用 419 条 golden 样本对比，结果 100% 一致。旧服务器不认识 `payment_sources`，装上不会被调用。

## 2026-10-29 · 配合 TelePilot 0.110.0-beta.2（通用规则页与表单契约 2）

- `auto_reply` 1.1.0、`autorepeat` 1.1.0、`chatgpt_image` 0.2.0、`codex_image` 1.2.0、`game24` 1.1.17 改用通用规则页 / 表单契约 2 的声明（`x-rule-schema`、`x-rule-summary`、`x-dry-run`、`x-ui-visible-if`、`dry_run_rule`）。

## 2026-10-28 · 配合 TelePilot 0.106.0-beta.17（访客回复回调按钮）

- `interaction-ai-chat` 0.4.8：按能力标记 `ctx.messages.guest_reply_markup` 在访客模式失败时也附「🔄 重试」「🔀 换模型重试」（换模型仅管理员）；旧服务器保持不附按钮。

## 2026-10-27 · 宿主兼容性体检

- 新增宿主兼容性检查 `scripts/check_host_compat.py` 与 CI（检出 TelePilot `Beta` 运行），修正 37 个插件的最低版本、补权限与 `manifest.py` 一致性；之后支持 `--plugins-root` 检查任意插件目录（如主仓库示例）。用法见 TelePilot 插件开发文档。

## 2026-10-26 · 配合 TelePilot 0.106.0-beta.2（访客 inline Rich 编辑）

- `interaction-ai-chat` 0.4.3：优先用 Rich Message 编辑访客 inline 消息（“🤔 占位后编辑”的表格、标题原生渲染），失败时退回 HTML。

## 2026-07-04 · 配合 TelePilot 0.49.0（插件开发指南契约）

- 远程插件库按当时最新开发指南完成全量契约迁移：补齐 `usage`、`event_subscriptions`、`capabilities`，移除旧 notice 通道声明，并为交互插件补充 `session_expired` 会话过期兼容。

## 2026-05-29 · 配合 TelePilot 0.26.1（交互入口失败不计次）

- `pt_promote` 1.0.6：置顶失败、重复触发或已冷却时返回业务失败，配合宿主修复后不再消耗用户每日次数。
