# 更新日志

## 1.0.11 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。

## 1.0.10 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 `bbot_notice`。
- 保留 `interaction_bot` 与 `userbot_reply` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。


## 1.0.9 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.0.8 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。

## 1.0.7 (2026-06-25)
- 修复交互 Bot 模式下奖励由 Bot 发放的问题，奖励消息改用 `send_via: userbot_reply` 由管理员账号发放。
- 交互发起的游戏 on_message 不再重复响应，避免双重提示。

## 1.0.6 (2026-06-19)
- 按 TelePilot 最新交互 Bot 入口规范补齐 `launch_mode`、事件白名单、会话策略、payload/result contract 和结算声明。
- 保留原有 UserBot 命令触发，交互 Bot 入口只负责触发和高频互动承接，不改变插件本体配置。
- 按最新插件开发指南补充 `min_telepilot_version`，并将配置页模式从旧兼容别名 `schema` 更新为推荐的 `single`。
- 交互 Bot 入口现在可以直接开局并处理后续消息，中奖/胜负结果返回独立的 `result` 与 `settlement` 字段。

## 1.0.5 (2026-06-19)
- 修复抢答赢家展示名可能读取账号本地联系人备注的问题；保存为联系人时优先展示公开 username（不带 @），没有 username 时回退用户 ID。
- 兼容新版 TelePilot 的统一公开展示名 helper，并保留旧环境兜底逻辑。

## 1.0.4 (2026-05-20)
- 补充交互 Bot 运行入口：实现 `on_interaction(ctx, entry_key, payload)` 最小 hook。
- 保持原有指令与消息监听主逻辑不变，仅新增交互入口声明对应的标准动作返回。
- 同步更新版本号与远程元数据一致性。

## 1.0.3 (2026-05-20)
- 新增模块分类声明：`category = "interactive"`（互动娱乐）。
- 声明交互 Bot 启动入口（interaction_entries）。
- 同步更新远程元数据 `plugin.json`，保证 manifest 与 metadata 一致。
