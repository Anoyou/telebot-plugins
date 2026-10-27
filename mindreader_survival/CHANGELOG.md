# 更新日志

## 1.1.14 (2026-10-28)
- 兼容性：`min_telepilot_version` 提高到 `0.72.0-beta.2`。`manifest.py` 向 `Manifest` 传入了 `requires_platform_capabilities`，该参数 TelePilot 0.72.0 起才支持，更早的版本导入插件时直接报 `TypeError`（宿主兼容性检查在 0.59.1 / 0.71.3 上实测）；提高后旧版本会在加载前给出“请先更新 TelePilot”的明确提示。功能无变化。

## 1.1.13 (2026-10-26)
- 配置表单：触发指令名（`command`）、门票价格（金币）（`ticket_price`）、总轮数（`total_rounds`）、每轮选择限时（秒）（`round_timeout`）、选项词库（逗号分隔）（`option_word_pool`）已有默认值，从 `required` 中移除，不再被 App 标成「必填」；留空或不填时运行时仍按默认值处理，不改变任何业务逻辑。

## 1.1.12 (2026-08-15)
- 新增标准 Event Bus 主入口，发送操作统一改走 MessageOps，并补充人工核销场景所需 ledger 能力。

## 1.1.11 (2026-08-15)
- 补充 TelePilot 0.97.0 平台能力声明，并同步 plugin.json 与 manifest.py。


## 1.1.10 (2026-07-12)
- 移除仅存在于 `manifest.py`、但运行时未开放对应配置能力的文案模板字段，恢复 Manifest 与 `plugin.json` 的一致性。
- 游戏规则、结算和现有可配置项保持不变。

## 1.1.9 (2026-07-10)
- 按最新插件开发指南对齐：`on_interaction` 相关 payload 读取（事件类型、chat_id、user_id、message_id、显示名、用户名、文本、付款金额）主路径改用标准事件信封 `event_from_interaction_payload`，旧平铺 payload 字段保留为 fallback。
- 元数据同步：`plugin.json` 与 `manifest.py` 版本递增并保持一致。
- 保持原有读心生存赛游戏规则、结算与文案逻辑不变；进程内 `self._sessions` 状态机保留（未做 Tier 3 迁移）。

## 1.1.8 (2026-07-04)
- 移除旧 `result_contract.send_via` 样板，普通回复改为继承当前会话通道。
- 更新使用说明，按最新文档区分普通会话通道与 userbot/payout 资金链路。

## 1.1.7 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。

## 1.1.6 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 旧 notice 通道值。
- 保留 `interaction_bot` 与 `userbot_reply` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。



## 1.1.5 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.1.4 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。
- 补齐远程安装阶段需要的静态元数据，避免插件市场只读取 `plugin.json` 时缺少入口、权限或命令信息。

## 1.1.3 (2026-06-25)
- 修复玩家在 UserBot 普通消息路径发送数字选择时调用缺失 `_handle_choice` 导致插件报错的问题。
- 普通消息数字选择现在复用交互 Bot 的答题逻辑，保证两条入口的选择记录行为一致。
- 修复轮次超时配置属性遮挡同名超时任务方法，导致开局创建超时任务时可能报错的问题。

## 1.1.2 (2026-06-25)
- 按 TelePilot 最新交互 Bot 入口规范补齐玩法入口声明和结算结果。
