# 更新日志

## 0.1.2 (2026-06-30)
- 按最新远程插件规范移除对 `app.db.*` 与 `app.services.*` 内部模块的直接 import。
- Token 池命令改为只更新本次运行时配置，并在命令返回中提示需要到插件配置页同步保存，避免插件跨层持久化 `account_feature.config`。
- 使用说明示例统一改为 `{prefix}` 占位符，并调整 `httpx.Timeout` 写法以消除远程插件 lint 警告。
- 同步 `plugin.json`、`manifest.py` 和运行时版本常量。

## 0.1.1 (2026-06-30)
- 按最新插件开发规范在 `config_schema` 顶层补充 `x-usage-guide`，让插件中心和配置页能稳定展示详细使用说明。
- 同步 `plugin.json`、`manifest.py` 和运行时版本常量。
