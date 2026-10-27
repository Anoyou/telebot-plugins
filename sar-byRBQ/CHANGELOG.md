# 更新日志

## 1.0.11 (2026-10-28)
- 兼容性：`min_telepilot_version` 提高到 `0.72.0-beta.2`。`manifest.py` 向 `Manifest` 传入了 `requires_platform_capabilities`，该参数 TelePilot 0.72.0 起才支持，更早的版本导入插件时直接报 `TypeError`（宿主兼容性检查在 0.59.1 / 0.71.3 上实测）；提高后旧版本会在加载前给出“请先更新 TelePilot”的明确提示。功能无变化。

## 1.0.10 (2026-10-26)
- 配置表单：触发指令名（`command`）、是否启用（`enabled`）已有默认值，从 `required` 中移除，不再被 App 标成「必填」；留空或不填时运行时仍按默认值处理，不改变任何业务逻辑。

## 1.0.9 (2026-08-15)
- 将消息、文件发送和删除迁移到 MessageOps；无标准 sticker 动作时安全拒绝直连。


## 1.0.7 (2026-07-14)
- 首次启动时若新目录尚无配置，原子复制源码目录中的旧贴纸自动回复配置到 `ctx.data_dir` 账号目录；不覆盖新文件且保留旧文件。

## 1.0.6 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。


## 1.0.5 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.0.4 (2026-06-27)
- 按 TelePilot 0.33 插件开发文档更新远程元数据，将最低 TelePilot 版本提升到 `0.33.0`。
- 同步 `plugin.json` 与 `manifest.py` 版本，保持配置页模式和插件分类声明一致。

## 1.0.3 (2026-06-19)
- 按最新插件开发指南补充 `min_telepilot_version`，并将配置页模式从旧兼容别名 `schema` 更新为推荐的 `single`。

## 1.0.2 (2026-05-20)
- 新增模块分类声明：`category = "automation"`（自动化）。
- 未声明交互 Bot 启动入口（interaction_entries 为空）。
- 同步更新远程元数据 `plugin.json`，保证 manifest 与 metadata 一致。

## 1.0.1 - 2026-05-17

- 完善插件市场描述，明确贴纸自动回复和群级开关能力。
- 配套补充 BYRBQ 插件手工测试清单与静态检查脚本，便于发布前验证。
