# 更新日志

## 0.1.6 (2026-08-15)
- 图片状态编辑与结果发送统一改用平台 MessageOps，ChatGPT 请求与远程导入接入 ctx.http 白名单；受 TelePilot 0.97.0 facade 限制，参考图 PUT 上传会明确拒绝。

## 0.1.5 (2026-08-15)
- 补充 TelePilot 0.97.0 平台能力声明，并同步 plugin.json 与 manifest.py。


## 0.1.4 (2026-07-14)
- 修正运行时版本常量滞后于 `plugin.json` 与 `manifest.py` 的问题，三处版本统一为 `0.1.4`。

## 0.1.3 (2026-07-10)
- 按最新插件开发指南对齐元数据：`plugin.json` 与 `manifest.py` 补充 `min_telepilot_version="0.33.0"` 并同步版本号。
- 未改动命令、Token 池、图片生成/编辑逻辑与文案。

## 0.1.2 (2026-06-30)
- 按最新远程插件规范移除对 `app.db.*` 与 `app.services.*` 内部模块的直接 import。
- Token 池命令改为只更新本次运行时配置，并在命令返回中提示需要到插件配置页同步保存，避免插件跨层持久化 `account_feature.config`。
- 使用说明示例统一改为 `{prefix}` 占位符，并调整 `httpx.Timeout` 写法以消除远程插件 lint 警告。
- 同步 `plugin.json`、`manifest.py` 和运行时版本常量。

## 0.1.1 (2026-06-30)
- 按最新插件开发规范在 `config_schema` 顶层补充 `x-usage-guide`，让插件中心和配置页能稳定展示详细使用说明。
- 同步 `plugin.json`、`manifest.py` 和运行时版本常量。
