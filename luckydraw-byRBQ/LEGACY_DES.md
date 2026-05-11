# 自动抽奖插件 (LuckyDraw)

在指定群组中自动识别红包/抽奖活动并发送口令参与，支持机器人白名单、群组独立延时与中奖庆祝贴纸。

## 功能特点

- 支持多种口令格式识别
- 脚本检测规避（敏感词过滤）
- 随机延迟发送（0.5-2秒）
- 群组独立开关
- 群组延时独立配置
- 抽奖机器人白名单
- 中奖后随机发送庆祝贴纸
- 统计信息

## 使用方法

1. 在群组中使用 `,ldraw on` 启用功能
2. 当白名单机器人发起红包/抽奖活动时，插件会自动识别并发送口令
3. 如果提前配置了庆祝贴纸，检测到自己中奖后会自动延迟发送一个随机贴纸

## 支持的口令格式

- `领取密令: xxx`
- `参与关键词：「xxx」`
- `发送 xxx 进行领取`
- `口令: xxx`

## 管理命令

- `,ldraw on` - 启用当前群组
- `,ldraw off` - 禁用当前群组
- `,ldraw set <群组ID>` - 手动添加群组
- `,ldraw list` - 查看已启用群组
- `,ldraw delayset <群组ID> <最小延时> [最大延时]` - 设置指定群组延时
- `,ldraw delayoff <群组ID>` - 移除指定群组延时
- `,ldraw bot list` - 查看抽奖机器人白名单
- `,ldraw bot add <bot_id>` - 添加机器人到白名单
- `,ldraw bot del <bot_id>` - 从白名单移除机器人
- `,ldraw sticker list` - 查看庆祝贴纸列表
- `,ldraw sticker add` - 回复贴纸添加到庆祝列表
- `,ldraw sticker del <序号>` - 删除指定庆祝贴纸
- `,ldraw sticker clear` - 清空庆祝贴纸
- `,ldraw clear` - 清除已发送口令记录
- `,ldraw stats` - 查看统计
- `,ldraw test <文本>` - 测试口令提取
