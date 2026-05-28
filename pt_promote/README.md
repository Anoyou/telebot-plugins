# PT 种子置顶促销模块

在青娃PT (qingwapt.com) 上为种子设置置顶促销，消耗蝌蚪。

## 功能

- **置顶促销**：为种子设置置顶促销，支持自定义类型、时长、竞价和奖励
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
{prefix}pt <种子ID> [选项]
```

**选项：**

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `free` | 促销类型：Free | ✅ 默认 |
| `2x` | 促销类型：2X Free | |
| `1d` | 时长：1天 | ✅ 默认 |
| `2d` | 时长：2天 | |
| `3d` | 时长：3天 | |
| `7d` | 时长：7天 | |
| `bid=100` | 竞价蝌蚪（越高排名越靠前） | 0 |
| `reward=50` | 奖励蝌蚪（吸引下载者） | 0 |
| `users=10` | 奖励人数 | 0 |

**示例：**

```bash
# 默认：Free 1天
{prefix}pt 12345

# Free 7天
{prefix}pt 12345 free 7d

# 2X Free 3天，竞价200蝌蚪
{prefix}pt 12345 2x 3d bid=200

# Free 7天，竞价100，奖励50蝌蚪给10个下载者
{prefix}pt 12345 free 7d bid=100 reward=50 users=10
```

### 激励机制

- **竞价蝌蚪 (`bid`)**：越高排名越靠前，其他用户的置顶会排在你后面
- **奖励蝌蚪 (`reward`)**：发放给下载此种子的用户，吸引更多人下载
- **奖励人数 (`users`)**：奖励发放给多少人

### 查询历史

```
{prefix}ptinfo <种子ID>
```

## 权限要求

- 需要青娃PT账号
- 操作会消耗蝌蚪（站点虚拟货币）

## API 说明

本模块通过以下 API 与青娃PT交互：

1. `GET /plugin/sticky-promotion-info?torrent_id=<id>` — 获取促销信息
2. `POST /plugin/sticky-promotion?...&__just_calculate=1` — 预计算消耗
3. `POST /plugin/sticky-promotion?...` — 确认促销

## 注意事项

- Cookie 会过期，过期后需重新获取
- 每次促销会消耗蝌蚪，具体数量由站点计算
- 到期后若未能下载完成，蝌蚪不退
