## 1.1.0 (2026-10-29)
- 改用 TelePilot 通用规则页（0.110.0-beta.2+）：`config_schema` 声明 `x-ui-mode=rules`，每条规则的表单写在 `x-rule-schema`（匹配类型、作用范围、指定群只在选「指定群」时显示、模式、回复内容、冷却与上限、高级），列表摘要 `x-rule-summary`，试运行卡片 `x-dry-run`（样例消息 + 会话类型 + 会话 ID）。字段名与旧专属页写入的完全相同，存量规则无需迁移。
- 新增 `dry_run_rule(config, sample)`：宿主新试运行协议，返回命中、渲染结果和逐步解释；旧宿主继续调用 `_dry_run_match`，两者结论一致。
- `min_telepilot_version` 不变：旧宿主不认识这些声明，Web 继续用旧专属页，运行逻辑不受影响。

## 1.0.3 (2026-08-15)
- 将规则回复、通知和自动命令结果统一改为平台 MessageOps，并保留自动命令派发兼容桥。

## 1.0.4 (2026-10-28)
- 兼容性：`min_telepilot_version` 提高到 `0.72.0-beta.2`。`manifest.py` 向 `Manifest` 传入了 `requires_platform_capabilities`，该参数 TelePilot 0.72.0 起才支持，更早的版本导入插件时直接报 `TypeError`（宿主兼容性检查在 0.59.1 / 0.71.3 上实测）；提高后旧版本会在加载前给出“请先更新 TelePilot”的明确提示。功能无变化。
- 补声明 `resolve_entity` 权限：规则里按 `@用户名` 指定用户时需要解析实体，之前未声明会被 TelePilot 沙箱拒绝、静默解析失败。

## 1.0.2 (2026-08-15)
- 补充 TelePilot 0.97.0 平台能力声明，并同步 plugin.json 与 manifest.py。

# 自动回复 更新日志

## 1.0.1 (2026-07-10)

- 按最新插件开发指南对齐：补齐 `min_telepilot_version="0.33.0"`（`plugin.json` 与 `manifest.py` 同步），元数据一致性校验通过。
- 保持原有自动回复规则匹配、冷却与每日限额逻辑及文案不变。

## 1.0.0

- 新增插件库推荐自动回复插件，支持按规则匹配关键词或正则后回复目标会话。
- 补齐插件中心 `usage`、事件订阅和能力声明，规则保存后随账号 worker 热更新生效。
