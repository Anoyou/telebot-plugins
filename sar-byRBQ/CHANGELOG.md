# 更新日志
## 1.0.3 (2026-06-19)
- 按最新插件开发指南补充 `min_telepilot_version`，并将配置页模式从旧兼容别名 `schema` 更新为推荐的 `single`。

## 1.0.2 (2026-05-20)
- 新增模块分类声明：`category = "automation"`（自动化）。
- 未声明交互 Bot 启动入口（interaction_entries 为空）。
- 同步更新远程元数据 `plugin.json`，保证 manifest 与 metadata 一致。

## 1.0.1 - 2026-05-17

- 完善插件市场描述，明确贴纸自动回复和群级开关能力。
- 配套补充 BYRBQ 插件手工测试清单与静态检查脚本，便于发布前验证。