## 1.1.0 (2026-10-29)
- 通用规则页（TelePilot 0.110.0-beta.2+）：声明列表摘要 `x-rule-summary`（群组 / 时间窗口 / 人数）与试运行卡片 `x-dry-run`；新增 `dry_run_rule(config, sample)`。
- `min_telepilot_version` 不变，旧宿主行为不变。

## 1.0.4 (2026-08-15)
- 将自动复读发送和管理命令编辑统一改为平台 MessageOps。

## 1.0.5 (2026-10-28)
- 兼容性：`min_telepilot_version` 提高到 `0.72.0-beta.2`。`manifest.py` 向 `Manifest` 传入了 `requires_platform_capabilities`，该参数 TelePilot 0.72.0 起才支持，更早的版本导入插件时直接报 `TypeError`（宿主兼容性检查在 0.59.1 / 0.71.3 上实测）；提高后旧版本会在加载前给出“请先更新 TelePilot”的明确提示。功能无变化。

## 1.0.3 (2026-08-15)
- 补充 TelePilot 0.97.0 平台能力声明，并同步 plugin.json 与 manifest.py。

# 自动复读 更新日志

## 1.0.2 (2026-07-10)

- 按最新插件开发指南对齐：补齐 `min_telepilot_version="0.33.0"`（`plugin.json` 与 `manifest.py` 同步），元数据一致性校验通过。
- 保持原有多人重复发言统计、每日复读限制与忽略规则逻辑及文案不变（帮助文案已使用 `{prefix}` 模板）。

## 1.0.1

- 新增插件库推荐自动复读插件，支持按群组规则统计多人重复发言并自动复读。
- 补齐插件中心 `usage`、规则配置说明、事件订阅和能力声明。
