# PT 种子置顶促销模块

在青娃PT (qingwapt.com) 上为种子设置置顶促销，消耗蝌蚪。

## 功能

- **置顶促销**：为种子设置置顶促销，消耗蝌蚪
- **查询历史**：查看种子的促销记录

## 配置

在 TelePilot Web 界面的模块配置中设置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| 触发指令名 | 不含系统前缀的命令名 | `pt` |
| PT 站点地址 | 站点根 URL | `https://www.qingwapt.com` |
| Cookie | 浏览器登录后的完整 Cookie | （必填） |

### 获取 Cookie

1. 在浏览器中登录青娃PT
2. 按 F12 打开开发者工具
3. 切换到 Network 标签
4. 刷新页面，点击任意请求
5. 在 Request Headers 中找到 `Cookie` 字段
6. 复制完整的 Cookie 值

## 使用方法

### 置顶促销

```
{prefix}pt <种子ID>
```

示例：
- `{prefix}pt 12345` — 为种子 12345 设置置顶促销

执行后会：
1. 获取促销表单（使用站点默认参数）
2. 预计算消耗的蝌蚪数
3. 确认并完成促销

### 查询历史

```
{prefix}ptinfo <种子ID>
```

示例：
- `{prefix}ptinfo 12345` — 查看种子 12345 的促销记录

## 权限要求

- 需要青娃PT账号
- 操作会消耗蝌蚪（站点虚拟货币）

## API 说明

本模块通过以下 API 与青娃PT交互：

1. `GET /plugin/sticky-promotion-info?torrent_id=<id>` — 获取促销信息和表单
2. `POST /plugin/sticky-promotion?...&__just_calculate=1` — 预计算消耗
3. `POST /plugin/sticky-promotion?...` — 确认促销

## 注意事项

- Cookie 会过期，过期后需重新获取
- 每次促销会消耗蝌蚪，具体数量由站点计算
- 表单参数使用站点默认值（如天数等）
