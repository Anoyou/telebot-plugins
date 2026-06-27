# 更新日志



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
