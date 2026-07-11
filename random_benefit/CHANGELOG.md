# 更新日志

## 1.1.0 (2026-07-11)
- 声明 `capabilities.telegram_direct_passthrough`，允许 TelePilot 在账号二次开启 `direct_passthrough.enabled=true` 后，把 userbot incoming 原始消息投递到 `on_direct_message`。
- 新增裸直通入口：命中目标群组、开关状态和随机概率后，直接通过 Telethon event 引用回复随机福利语，用于测试裸直通链路是否生效。
- 保留标准 Event Bus 命令与消息入口，`on/off/status` 仍可通过原标准链路控制当前群组开关。
- 同步更新配置页使用说明，提示裸直通需要账号配置二次开启。

## 1.0.0 (2026-07-11)
- 新增“随机福利”插件：可在配置页从当前账号“已允许会话”选择目标群组，群组默认开启监听。
- 支持自定义触发指令名，管理员可在群内通过 `on`、`off`、`status` 开启、暂停或查看当前群组状态。
- 支持自定义随机回复语，默认回复 `+1-6666`，命中后引用被抽中的发言发送。
- 配置页补充只读使用说明和 `template_preview`，支持 TelePilot 通用“插件预览”卡片。
