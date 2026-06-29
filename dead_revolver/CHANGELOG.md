# 更新日志

## 1.0.5 (2026-06-29)
- 修复玩家超时自射导致结算流程取消自身定时任务，从而中断最终结算的问题；只剩一名存活玩家时会正常结算并发放奖励。
- 发送下一名玩家射击按钮前会清理上一轮按钮消息，减少群聊中已完成操作的旧按钮残留。
- 报名付款会校验收款人是否为当前 UserBot，并在通知缺少 reply_id 时回退到付款消息发送者，降低误报名和漏报名概率。
- 游戏取消或人数不足时自动退还已付门票，并更新大厅参与说明，隐藏内部游戏 ID。
- 同步声明 `开始挑战` 开局别名，保持插件元数据与运行时命令一致。

## 1.0.4 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。

## 1.0.3 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 `bbot_notice`。
- 保留 `interaction_bot` 与 `userbot_reply` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。


## 1.0.2 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.0.1 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。
