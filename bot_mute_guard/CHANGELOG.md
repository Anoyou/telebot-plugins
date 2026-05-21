# 更新日志

## 1.0.2 (2026-05-21)
- 优化 Bot 白名单配置说明：明确推荐每行填写一个 Bot username，`@` 可写可不写，大小写不敏感。
- 同步更新运行日志版本号与远程元数据，便于确认 TelePilot 已加载新版本。

## 1.0.1 (2026-05-21)
- 按 TelePilot 0.19.2 远程模块开发文档严格兼容：改用 `min_telepilot_version`、`category = "automation"`、`x-ui-mode = "single"`，并同步补充兼容展示用的 `x-category`，保持 `plugin.json` 与 `manifest.py` 元数据一致。
- 移除远程模块沙箱未授权的成员管理路径，不再调用 `get_entity`、`edit_permissions` 或 raw MTProto；模块只使用已声明的 `delete_message` 与 `send_message` 能力。
- 将非白名单 `@xxxbot`、inline Bot 与可识别 Bot 发言的处理收敛为删除违规消息、写运行日志和可选群内提示，避免越过 TelePilot 权限边界导致运行失败。
- 更新冒烟测试，覆盖非白名单 `@dddbot` 删除、白名单 `/命令@qqqbot` 忽略、纯 `@defbot` 删除以及邮箱样式不误删。

## 1.0.0 (2026-05-21)
- 新增 Bot 防广告守卫插件草案：支持目标群、Bot 白名单、inline Bot 处理与演练模式配置。
