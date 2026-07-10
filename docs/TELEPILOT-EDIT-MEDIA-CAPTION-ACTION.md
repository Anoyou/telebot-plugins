# TelePilot 标准动作支持媒体 Caption 编辑建议

## 背景

九宫格猜骰这类插件会先发送一张题图，再把题面说明放在图片 caption 里。UserBot 命令路径目前可以直接编辑这条带图消息的 caption，所以答对后能把原题面改成“题面 + 答对结果”，群里不会多出一条结果公告。

交互 Bot 标准链路目前只能稳定编辑纯文本消息。插件如果返回 `edit_message` action，平台底层会走 Bot API 的 `editMessageText`；当目标消息是 `send_photo` 发出的图片消息时，Telegram 需要调用 `editMessageCaption`。这会导致交互 Bot 插件无法在不绕过 MessageOps 的前提下复用图片题面消息。

插件侧不应该直接调用 live client 或 Bot API 来绕过平台。正确方向是让 TelePilot 的标准 action 支持媒体 caption 编辑，并继续由 Delivery Executor 统一执行、审计、限速和记录 Trace。

## 目标

1. 标准 action 可以编辑 `send_photo` / `send_file` 发送出的媒体消息 caption。
2. 插件仍只返回标准 action 或通过 `ctx.messages` 生成 action，不直接接触 Bot Token、userbot session 或 live client。
3. 现有 `edit_message` 文本编辑行为保持兼容。
4. 失败时进入现有 action trace 和 runtime log，不能静默丢失。
5. `payout`、收付款、发奖链路不受影响，仍固定由 userbot 执行。

## 建议的 Action 契约

可以选择扩展现有 `edit_message`，也可以新增 `edit_caption`。更推荐新增 `edit_caption`，边界更清楚，也避免 `text` / `caption` 混用导致执行器猜测目标类型。

### 新增 `edit_caption`

```json
{
  "type": "edit_caption",
  "chat_id": -1001234567890,
  "message_id": 123,
  "caption": "<b>九宫格竞猜</b>\n目标：<b>22</b> · 回 <code>1-9</code>\n奖 <b>+1000</b> · 90s · 冷却 5.0s\n\nuhaveanswer 答对：<b>图 2</b>\n用时 54.1s · 奖励 <b>+1000</b>",
  "parse_mode": "html",
  "reply_markup": null
}
```

字段建议：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `type` | 是 | 固定为 `edit_caption` |
| `chat_id` | 否 | 不填时沿用当前 incoming chat |
| `message_id` | 是 | 要编辑的媒体消息 ID |
| `caption` | 是 | 新 caption，支持 plain/html |
| `parse_mode` | 否 | 默认 plain；html 时按现有 HTML 转义规则处理 |
| `reply_markup` | 否 | 可选 inline keyboard |
| `send_via` / `channel` | 否 | 复用现有通道选择逻辑；普通插件默认不写 |

### BufferedMessageOps facade

建议给 `ctx.messages` 增加：

```python
await ctx.messages.edit_caption(
    chat_id=chat_id,
    message_id=message_id,
    caption=caption,
    parse_mode="html",
    reply_markup=reply_markup,
)
```

生成 action：

```python
{
    "type": "edit_caption",
    "chat_id": chat_id,
    "message_id": message_id,
    "caption": caption,
    "parse_mode": "html",
}
```

## Delivery Executor 改造建议

在 `InteractionDeliveryExecutor.apply_actions` 中把 `edit_caption` 纳入受控消息动作：

```python
if action_type == "edit_caption":
    await self._apply_edit_caption(action, parse_mode=parse_mode, reply_markup=reply_markup)
    continue
```

新增 `_apply_edit_caption`，行为对齐 `_apply_edit_message`：

1. 校验 caption 非空。
2. 校验 message_id 存在。
3. 解析 chat_id，默认当前 incoming chat。
4. 走 `_try_edit_caption_options`，复用 `action_send_via_options(action)`。
5. 记录 `record_action`，失败时写 `error_code` 和 Telegram 错误。

`account_bot_service` 需要增加 Bot API 包装：

```python
async def edit_message_caption(
    token: str,
    chat_id: int,
    message_id: int,
    caption: str,
    *,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": caption,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await call_bot_api(token, "editMessageCaption", payload)
```

如果当前通道是 userbot，也可以在 worker action 里映射成 Telethon 的 `edit_message(chat_id, message_id, caption, parse_mode=...)`。不过交互 Bot 的主要需求是 Bot API 的 `editMessageCaption`。

## 与 `send_photo` 的 message_id 保存配合

现有标准动作已经有 `save_message_id_key`，建议确认 `send_photo` / `send_file` 成功后也写入该 key。插件就可以这样工作：

```python
return [
    {
        "type": "send_photo",
        "photo_base64": image_base64,
        "filename": "dice_grid_hunt.png",
        "caption": render_round(rd),
        "parse_mode": "html",
        "save_message_id_key": round_message_key,
    }
]
```

答对后读取同一个 key，再返回：

```python
return [
    {
        "type": "edit_caption",
        "chat_id": chat_id,
        "message_id": round_message_id,
        "caption": render_round(rd) + "\n\n" + render_success(rd),
        "parse_mode": "html",
    },
    {
        "type": "payout",
        "chat_id": chat_id,
        "amount": rd.prize,
        "text": f"+{rd.prize}",
        "reply_to_message_id": winner_message_id,
        "reply_to_user_id": winner_user_id,
    },
    {"type": "end_session"}
]
```

如果平台暂时不想让插件直接读 Redis 里的保存 key，也可以扩展 action 支持 `message_id_key`：

```json
{
  "type": "edit_caption",
  "chat_id": -1001234567890,
  "message_id_key": "dice_grid_hunt:1:-100123:round",
  "caption": "..."
}
```

Delivery Executor 内部按 account namespace 读取 key，读不到时返回 `target_message_id_missing`。这个做法对第三方插件更干净，因为插件不需要知道 `tp:msgid:{account_id}:...` 的内部 Redis 命名。

## 兼容策略

1. `edit_message` 保持现状，继续编辑纯文本消息。
2. `edit_caption` 是新增 action，旧插件不受影响。
3. `result_contract.actions` 未声明 `edit_caption` 时，平台按现有 Contract Guard 规则记录告警，不应静默丢弃。
4. `ctx.messages.edit_caption` 只缓存 action，不直接发请求。
5. 如果 Telegram 返回 “message is not modified”，可以记录为成功或可忽略 warn，避免重复编辑导致用户可见失败。
6. 如果 Telegram 返回 “there is no caption in the message to edit”，仍可以调用 `editMessageCaption` 设置新 caption；Bot API 支持给媒体消息新增 caption。
7. 如果目标消息不是媒体消息，应返回失败并写 trace，不 fallback 成普通群消息，避免插件误以为已经编辑成功。

## 安全边界

- 普通互动插件仍不写 `send_via`，由会话通道决定实际发送方。
- `payout` 不进入 `edit_caption` 逻辑，仍由 userbot 固定执行。
- `caption` 的 HTML 规则和当前 `send_message` 一致：默认 plain，显式 html 时插件负责转义变量。
- 不把 Bot Token、原生 update、完整私聊文本写入日志。
- action 失败要能在 Trace 里看到 action type、chat_id、message_id、error_code 和 Telegram 错误摘要。

## 验收用例

### 1. 交互 Bot 编辑图片 caption

流程：

1. 插件返回 `send_photo`，带 `caption`、`parse_mode=html`、`save_message_id_key=round`。
2. 平台成功发送图片，并保存 message_id。
3. 玩家答对后，插件返回 `edit_caption`，目标为上一步图片消息。

预期：

- 群里原图片消息 caption 被更新为“题面 + 答对结果”。
- 不额外发送“答对了”公告。
- Trace 中有 `send_photo`、`edit_caption`、`payout`。

### 2. HTML caption 正常渲染

caption 包含 `<b>`、`<code>` 和用户昵称转义内容。

预期：

- HTML 正常渲染。
- 用户昵称里的 `<`、`>`、`&` 不破坏格式。

### 3. message_id 缺失

插件返回 `edit_caption`，但没有 `message_id`，或者 `message_id_key` 读不到。

预期：

- action 失败，error_code 为 `target_message_id_missing`。
- 后续 `payout` 是否继续执行按当前 action 执行策略保持一致。
- runtime log 能定位到缺失 key。

### 4. 错误目标消息

目标 message_id 指向纯文本消息。

预期：

- Telegram 返回错误，平台记录 action failed。
- 不自动改发普通消息。

### 5. 不影响发奖

九宫格猜骰答对后同时返回 `edit_caption` 和 `payout`。

预期：

- caption 更新成功。
- `payout` 仍由 userbot 回复赢家消息发出 `+金额`。
- 找不到赢家锚点时仍走现有 `reply_to_user_id` / `reply_anchor_missing_text` 失败处理。

## 插件迁移示例

九宫格猜骰迁移后，交互 Bot 开局可以恢复为单条图片题面：

```python
{
    "type": "send_photo",
    "photo_base64": image_base64,
    "filename": "dice_grid_hunt.png",
    "caption": self._render_round_text(rd, include_guide=True),
    "parse_mode": "html",
    "save_message_id_key": round_message_key,
}
```

答对后：

```python
{
    "type": "edit_caption",
    "chat_id": chat_id,
    "message_id": round_message_id,
    "caption": self._render_round_text(rd, include_guide=True) + "\n\n" + success_text,
    "parse_mode": "html",
}
```

这样交互 Bot 和 UserBot 的表现就一致：题图原地更新，发奖仍走平台受控 `payout`。
