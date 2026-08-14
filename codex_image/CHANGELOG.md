# 更新日志

## 1.1.6 (2026-08-15)
- 将 Codex 请求接入平台 ctx.http 白名单，并将图片发送、原消息编辑与删除统一改为 MessageOps。

## 1.1.5 (2026-08-15)
- 补充 TelePilot 0.97.0 平台能力声明，并同步 plugin.json 与 manifest.py。


## 1.1.4 (2026-07-10)
- 按最新插件开发指南对齐元数据：`plugin.json` 与 `manifest.py` 补充 `min_telepilot_version="0.33.0"` 并同步版本号。
- SSE 解析中的旧命名局部变量重命名为 `sse_event`，以通过安装校验门的废弃 token 扫描；纯局部改名，不改变解析逻辑与图片生成行为。
- 保持原有命令、参数、文案与生成逻辑不变。

## 1.1.3 (2026-06-30)
- 按最新远程插件规范移除对 `app.db.*` 与 `app.services.*` 内部模块的直接 import。
- 内置消息模板渲染逻辑，避免依赖平台内部 `llm_format` 服务。
- 参考图下载改用 TelePilot 公开的高层媒体 helper，不再直接调用下划线私有实现。
- Token 命令改为只更新本次运行时配置，并提示需要到插件配置页同步保存，避免插件跨层持久化 `account_feature.config`。
- 使用说明示例统一改为 `{prefix}` 占位符，并调整 `httpx.Timeout` 写法以消除远程插件 lint 警告。
- 同步 `plugin.json` 与 `manifest.py` 版本。

## 1.1.2 (2026-06-30)
- 按最新插件开发规范在 `config_schema` 顶层补充 `x-usage-guide`，让插件中心和配置页能稳定展示详细使用说明。
- 同步 `plugin.json` 与 `manifest.py` 版本。
