# -*- coding: utf-8 -*-
# 插件名称: luckydraw
# 生成时间: 2026-02-22 14:26:56

# -*- coding: utf-8 -*-
# 插件名称: luckydraw
# 生成时间: 2026-02-21 14:59:08

# -*- coding: utf-8 -*-
# 插件名称: luckydraw
# 生成时间: 2026-02-16 17:00

"""
自动抽奖插件 (LuckyDraw)
功能描述: 在指定群组中自动识别红包/抽奖活动并发送口令参与
文件名: luckydraw.py
"""

import asyncio
import json
import random
import re
from pathlib import Path
from typing import Dict, Optional, Set, List
from collections import defaultdict

from pagermaid.listener import listener
from pagermaid.hook import Hook
from pagermaid.enums import Message, Client
from pagermaid.utils import logs


# 配置文件路径
plugin_dir = Path(__file__).parent
config_file = plugin_dir / "luckydraw_config.json"

# 脚本检测关键词（出现这些词则不触发）
SCRIPT_DETECTION_KEYWORDS = [
    "脚本",
    "检测",
    "不能领",
    "禁止脚本",
    "防脚本",
    "robot",
    "bot",
    "auto",
    "脚本检测",
    "自动领",
    "作弊",
    "挂",
]
# 自排除关键词（消息包含这些词时不参与抽奖，避免尴尬）
SELF_EXCLUSION_KEYWORDS = [
    "我是G",
    "我是g",
    "自ban",
    "自ban人",
    "我是自ban人",
    "我是自ban",
    "我是狗",
    "我是畜生",
    "我是猪",
    "我是傻",
    "我是sb",
    "我是SB",
    "我是傻逼",
    "我是蠢",
    "我是废物",
    "我是垃圾",
    "/promote",
]

# 抢红包随机延迟范围（秒）- 默认值
DEFAULT_MIN_DELAY = 2.0
DEFAULT_MAX_DELAY = 5.0

# 等待其他用户回复的时间范围（秒）
WAIT_MIN_DELAY = 3.0
WAIT_MAX_DELAY = 8.0

# 需要等待的其他用户回复数量（随机1-2人）
WAIT_USER_COUNT_MIN = 1
WAIT_USER_COUNT_MAX = 2

# 群组默认延时配置
# 注意：默认设为 0，转发别人话做抽奖时默认不加延时
# 只有用户主动用 ldraw delay 命令设置后才加延时
DEFAULT_DELAY = 0.0  # 秒

# 红包个数阈值：小于此值直接发送关键词，大于等于此值用转发逻辑
REDPACKET_COUNT_THRESHOLD = 5

# 默认抽奖机器人ID白名单（首次使用时写入配置文件）
DEFAULT_BOT_WHITELIST: Set[int] = {
    6461022460,  # 抽奖机器人
}

# ========== 性能优化：批量刷盘配置 ==========
CONFIG_FLUSH_INTERVAL = 3.0  # 秒：多久强制刷盘一次
CONFIG_FLUSH_MAX_PENDING = 50  # 积累多少条变更后立即刷盘
# ==========================================


class LuckyDrawConfig:
    """自动抽奖配置管理类"""

    def __init__(self):
        self.enabled_chats: Set[int] = set()  # 启用功能的群组ID集合
        self.test_chats: Set[int] = set()  # 测试群组（输出详细日志）
        self.sent_keywords: Dict[str, list] = {}  # 已发送的口令 {群组ID: [口令1, 口令2, ...]}
        self.sent_messages: Set[str] = set()  # 已处理的消息ID {群组ID_消息ID}
        self.chat_delays: Dict[str, dict] = {}  # 群组延时配置 {群组ID: {"min": min_delay, "max": max_delay}}
        self.bot_whitelist: Set[int] = set()  # 抽奖机器人白名单
        self.celebration_stickers: Set[str] = set()  # 中奖庆祝贴纸 file_unique_id 集合
        self.stats: Dict[str, int] = {
            "total_detected": 0,  # 检测到的抽奖次数
            "total_joined": 0,    # 成功参与的次数
            "total_blocked": 0,   # 被安全检测拦截的次数
        }
        
        # ========== 性能优化：批量刷盘相关 ==========
        self._pending_save: bool = False  # 是否有待刷新的变更
        self._pending_keyword_changes: Dict[str, list] = {}  # 待刷新的口令变更
        self._pending_message_changes: Set[str] = set()  # 待刷新的消息ID变更
        self._pending_stats_changes: Dict[str, int] = {}  # 待刷新的统计变更
        self._flush_task: Optional[asyncio.Task] = None  # 刷盘定时任务
        self._change_count: int = 0  # 累计变更次数
        # ==========================================
        
        self.load()

    def load(self) -> None:
        """从文件加载配置"""
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.enabled_chats = set(data.get("enabled_chats", []))
                    self.test_chats = set(data.get("test_chats", []))
                    self.sent_keywords = data.get("sent_keywords", {})
                    self.sent_messages = set(data.get("sent_messages", []))
                    self.chat_delays = data.get("chat_delays", {})
                    self.bot_whitelist = set(data.get("bot_whitelist", DEFAULT_BOT_WHITELIST))
                    self.celebration_stickers = set(data.get("celebration_stickers", []))
                    self.stats = data.get("stats", self.stats)
            except Exception as e:
                logs.error(f"[LuckyDraw] 加载配置失败: {e}")
                self.enabled_chats = set()
                self.test_chats = set()
                self.sent_keywords = {}
                self.sent_messages = {}
                self.chat_delays = {}
                self.bot_whitelist = set(DEFAULT_BOT_WHITELIST)
                self.stats = {"total_detected": 0, "total_joined": 0, "total_blocked": 0}
        else:
            # 首次使用，使用默认白名单
            self.bot_whitelist = set(DEFAULT_BOT_WHITELIST)
            self.save()

    def _schedule_flush(self) -> None:
        """安排一次延迟刷盘（避免频繁写磁盘）"""
        self._pending_save = True
        self._change_count += 1
        
        # 如果变更太多，立即刷盘
        if self._change_count >= CONFIG_FLUSH_MAX_PENDING:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._flush_to_disk())
            except RuntimeError:
                # 初始化阶段没有事件循环，同步写入
                self._do_save()
            return
        
        # 启动/重置定时刷盘任务
        if self._flush_task is None or self._flush_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._flush_task = loop.create_task(self._flush_timer())
            except RuntimeError:
                pass  # 初始化阶段忽略，依赖下次主动保存
    
    async def _flush_timer(self) -> None:
        """定时刷盘协程"""
        await asyncio.sleep(CONFIG_FLUSH_INTERVAL)
        await self._flush_to_disk()
    
    async def _flush_to_disk(self) -> None:
        """将所有待刷新的变更写入磁盘"""
        if not self._pending_save:
            return
        
        # 合并变更
        for key, value in self._pending_keyword_changes.items():
            if key not in self.sent_keywords:
                self.sent_keywords[key] = []
            for v in value:
                if v not in self.sent_keywords[key]:
                    self.sent_keywords[key].append(v)
        self._pending_keyword_changes = {}
        
        self.sent_messages.update(self._pending_message_changes)
        self._pending_message_changes = set()
        
        for k, v in self._pending_stats_changes.items():
            self.stats[k] = self.stats.get(k, 0) + v
        self._pending_stats_changes = {}
        
        self._pending_save = False
        self._change_count = 0
        
        # 实际写入磁盘
        self._do_save()
    
    def _do_save(self) -> bool:
        """实际执行磁盘写入（同步）"""
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "enabled_chats": list(self.enabled_chats),
                        "test_chats": list(self.test_chats),
                        "sent_keywords": self.sent_keywords,
                        "sent_messages": list(self.sent_messages),
                        "chat_delays": self.chat_delays,
                        "bot_whitelist": list(self.bot_whitelist),
                        "celebration_stickers": list(self.celebration_stickers),
                        "stats": self.stats,
                    },
                    f,
                    indent=4,
                    ensure_ascii=False,
                )
            return True
        except Exception as e:
            logs.error(f"[LuckyDraw] 保存配置失败: {e}")
            return False

    def save(self) -> bool:
        """保存配置到文件（现在改为异步缓冲写入）"""
        self._schedule_flush()
        return True  # 返回成功，因为写入是异步的

    def add_chat(self, chat_id: int) -> str:
        """添加启用功能的群组"""
        if chat_id in self.enabled_chats:
            return f"群组 `{chat_id}` 已在启用列表中"
        self.enabled_chats.add(chat_id)
        self.save()
        return f"已启用群组 `{chat_id}` 的自动抽奖功能"

    def remove_chat(self, chat_id: int) -> str:
        """移除启用功能的群组"""
        if chat_id not in self.enabled_chats:
            return f"群组 `{chat_id}` 未启用此功能"
        self.enabled_chats.remove(chat_id)
        self.save()
        return f"已禁用群组 `{chat_id}` 的自动抽奖功能"

    def is_enabled(self, chat_id: int) -> bool:
        """检查群组是否启用功能"""
        return chat_id in self.enabled_chats

    def is_test_chat(self, chat_id: int) -> bool:
        """检查是否为测试群组"""
        return chat_id in self.test_chats

    def add_test_chat(self, chat_id: int) -> str:
        """添加测试群组"""
        if chat_id in self.test_chats:
            return f"群组 `{chat_id}` 已在测试群组列表中"
        self.test_chats.add(chat_id)
        self.save()
        return f"已添加群组 `{chat_id}` 到测试群组"

    def remove_test_chat(self, chat_id: int) -> str:
        """移除测试群组"""
        if chat_id not in self.test_chats:
            return f"群组 `{chat_id}` 不在测试群组列表中"
        self.test_chats.remove(chat_id)
        self.save()
        return f"已移除群组 `{chat_id}` 从测试群组"

    def get_chat_delay(self, chat_id: int) -> tuple[float, float]:
        """获取群组的延时配置 (min_delay, max_delay)"""
        key = str(chat_id)
        if key in self.chat_delays:
            return (
                self.chat_delays[key].get("min", DEFAULT_DELAY),
                self.chat_delays[key].get("max", DEFAULT_DELAY)
            )
        return (DEFAULT_DELAY, DEFAULT_DELAY)

    def set_chat_delay(self, chat_id: int, min_delay: float, max_delay: float = None) -> str:
        """设置群组的延时配置"""
        key = str(chat_id)
        if max_delay is None:
            max_delay = min_delay + 3.0  # 如果只设置一个值，范围为 [delay, delay+3]
        
        # 限制范围
        min_delay = max(0.1, min_delay)
        max_delay = max(min_delay, max_delay)
        
        self.chat_delays[key] = {"min": min_delay, "max": max_delay}
        self.save()
        return f"已设置群组 `{chat_id}` 延时为 {min_delay}~{max_delay} 秒"

    def remove_chat_delay(self, chat_id: int) -> str:
        """移除群组的自定义延时配置，恢复默认"""
        key = str(chat_id)
        if key in self.chat_delays:
            del self.chat_delays[key]
            self.save()
            return f"已移除群组 `{chat_id}` 的自定义延时，恢复默认 {DEFAULT_DELAY} 秒"
        return f"群组 `{chat_id}` 未设置自定义延时"

    def list_chat_delays(self) -> str:
        """列出所有群组的延时配置"""
        if not self.chat_delays:
            return "暂无自定义延时配置，默认延时 2 秒"
        
        output = "**群组延时配置列表：**\n\n"
        for chat_id, delay in self.chat_delays.items():
            output += f"- 群组 `{chat_id}`: {delay['min']}~{delay['max']} 秒\n"
        return output

    def has_sent_keyword(self, chat_id: int, keyword: str) -> bool:
        """检查口令是否已发送（同时检查内存和待刷新状态）"""
        key = str(chat_id)
        # 先检查已刷新到磁盘的记录
        if key in self.sent_keywords and keyword in self.sent_keywords[key]:
            return True
        # 再检查待刷新的变更（防止 flush 前重复触发）
        if key in self._pending_keyword_changes and keyword in self._pending_keyword_changes[key]:
            return True
        return False

    def mark_keyword_sent(self, chat_id: int, keyword: str) -> None:
        """标记口令已发送"""
        key = str(chat_id)
        # 立即更新内存状态，防止在 flush 前重复触发
        if key not in self.sent_keywords:
            self.sent_keywords[key] = []
        if keyword not in self.sent_keywords[key]:
            self.sent_keywords[key].append(keyword)
        # 同时记录待刷新变更
        if key not in self._pending_keyword_changes:
            self._pending_keyword_changes[key] = []
        if keyword not in self._pending_keyword_changes[key]:
            self._pending_keyword_changes[key].append(keyword)
        self._schedule_flush()

    def is_message_processed(self, chat_id: int, message_id: int) -> bool:
        """检查消息是否已处理"""
        key = f"{chat_id}_{message_id}"
        # 先检查待刷新的变更
        if key in self._pending_message_changes:
            return True
        return key in self.sent_messages

    def mark_message_processed(self, chat_id: int, message_id: int) -> None:
        """标记消息已处理"""
        key = f"{chat_id}_{message_id}"
        self._pending_message_changes.add(key)
        self._schedule_flush()

    def clear_sent_keywords(self, chat_id: int = None) -> str:
        """清除已发送口令记录"""
        if chat_id is None:
            self.sent_keywords = {}
            self._pending_keyword_changes = {}
            _processed_messages.clear()  # 清除所有进程内缓存
            self.save()
            return "已清除所有群组的口令记录"
        key = str(chat_id)
        if key in self.sent_keywords:
            del self.sent_keywords[key]
        if int(chat_id) in _processed_messages:
            del _processed_messages[int(chat_id)]
        self.save()
        return f"已清除群组 `{chat_id}` 的口令记录"

    def list_chats(self) -> str:
        """列出所有启用的群组"""
        if not self.enabled_chats:
            return "当前没有启用任何群组"

        output = "**已启用的群组列表：**\n\n"
        for chat_id in self.enabled_chats:
            output += f"- 群组ID: `{chat_id}`\n"
        return output

    # ========== 机器人白名单管理 ==========

    def add_bot(self, bot_id: int) -> str:
        """添加机器人到白名单"""
        if bot_id in self.bot_whitelist:
            return f"机器人 `{bot_id}` 已在白名单中"
        self.bot_whitelist.add(bot_id)
        self.save()
        return f"已添加机器人 `{bot_id}` 到白名单"

    def remove_bot(self, bot_id: int) -> str:
        """从白名单移除机器人"""
        if bot_id not in self.bot_whitelist:
            return f"机器人 `{bot_id}` 不在白名单中"
        self.bot_whitelist.remove(bot_id)
        self.save()
        return f"已从白名单移除机器人 `{bot_id}`"

    def is_bot_allowed(self, bot_id: int) -> bool:
        """检查机器人是否在白名单中"""
        return bot_id in self.bot_whitelist

    def list_bots(self) -> str:
        """列出所有白名单机器人"""
        if not self.bot_whitelist:
            return "当前白名单为空，所有抽奖消息都会被忽略"

        output = "**机器人白名单：**\n\n"
        for bot_id in self.bot_whitelist:
            output += f"- 机器人ID: `{bot_id}`\n"
        output += "\n💡 只有这些机器人发布的抽奖才会参与"
        return output

    # ========== 庆祝贴纸管理 ==========

    def add_sticker(self, file_unique_id: str) -> str:
        """添加庆祝贴纸"""
        if file_unique_id in self.celebration_stickers:
            return f"贴纸 `{file_unique_id}` 已在列表中"
        self.celebration_stickers.add(file_unique_id)
        self.save()
        return f"已添加贴纸 `{file_unique_id}` 到庆祝列表"

    def remove_sticker(self, file_unique_id: str) -> str:
        """移除庆祝贴纸"""
        if file_unique_id not in self.celebration_stickers:
            return f"贴纸 `{file_unique_id}` 不在列表中"
        self.celebration_stickers.remove(file_unique_id)
        self.save()
        return f"已从庆祝列表移除贴纸 `{file_unique_id}`"

    def list_stickers(self) -> str:
        """列出所有庆祝贴纸"""
        if not self.celebration_stickers:
            return "当前庆祝贴纸列表为空\n\n💡 使用 `,ldraw sticker add` 回复贴纸来添加"

        output = "**庆祝贴纸列表：**\n\n"
        for i, sticker_id in enumerate(self.celebration_stickers, 1):
            output += f"{i}. `{sticker_id}`\n"
        output += f"\n共 {len(self.celebration_stickers)} 个贴纸"
        output += "\n\n💡 中奖后会随机发送一个贴纸庆祝"
        return output

    def get_random_sticker(self) -> Optional[str]:
        """获取随机庆祝贴纸"""
        if not self.celebration_stickers:
            return None
        return random.choice(list(self.celebration_stickers))

    def get_stats(self) -> str:
        """获取统计信息"""
        output = "**统计信息：**\n\n"
        output += f"- 启用群组数: `{len(self.enabled_chats)}`\n"
        output += f"- 检测到的抽奖: `{self.stats['total_detected']}` 次\n"
        output += f"- 成功参与: `{self.stats['total_joined']}` 次\n"
        output += f"- 安全拦截: `{self.stats['total_blocked']}` 次\n"
        return output

    def increment_detected(self) -> None:
        """增加检测计数"""
        self._pending_stats_changes["total_detected"] = self._pending_stats_changes.get("total_detected", 0) + 1
        self._schedule_flush()

    def increment_joined(self) -> None:
        """增加参与计数"""
        self._pending_stats_changes["total_joined"] = self._pending_stats_changes.get("total_joined", 0) + 1
        self._schedule_flush()

    def increment_blocked(self) -> None:
        """增加拦截计数"""
        self._pending_stats_changes["total_blocked"] = self._pending_stats_changes.get("total_blocked", 0) + 1
        self._schedule_flush()


# 全局配置实例
config = LuckyDrawConfig()


# 待处理抽奖队列
# {chat_id_message_id: {"keyword": str, "keyword_type": str, "chat_id": int, "source_message_id": int}}
pending_draws: Dict[str, dict] = {}

# 群组+关键词级别的异步锁，防止并发重复发送
# {(chat_id, keyword): asyncio.Lock()}
keyword_locks: Dict[tuple, asyncio.Lock] = {}

# ========== 性能优化：进程内消息去重 ==========
# 用于快速判断消息是否已处理（进程内缓存，不依赖磁盘）
_processed_messages: Dict[int, Set[str]] = defaultdict(set)  # {chat_id: {message_id1, message_id2, ...}}
# ==========================================


def normalize_text(text: Optional[str]) -> str:
    """标准化文本，便于比较关键词"""
    if not text:
        return ""
    return re.sub(r"\s+", "", text).strip().lower()


def extract_red_packet_count(text: str) -> Optional[int]:
    """
    从红包消息中提取红包个数
    支持格式：
    - 📦 共3个
    - 共 3 个
    - 共3个
    - 共10个
    - 剩余2/3个 (新格式)
    - 自动开奖人数：10（抽奖消息专用）
    返回: 红包个数 或 None（无法解析）
    """
    if not text:
        return None

    # 优先匹配 "剩余X/Y个" 格式（取剩余个数）
    remaining_pattern = r"剩余\s*(\d+)\s*/\s*\d+\s*个"
    match = re.search(remaining_pattern, text)
    if match:
        count = int(match.group(1))
        if count > 0:
            return count

    # 匹配 "自动开奖人数：X" 格式（抽奖消息，判断是否用转发模式）
    auto_open_pattern = r"自动开奖人数[：:]\s*(\d+)"
    match = re.search(auto_open_pattern, text)
    if match:
        count = int(match.group(1))
        if count > 0:
            return count

    # 匹配 "共X个" 或 "共 X 个" 格式
    patterns = [
        r"共\s*(\d+)\s*个",  # 共3个, 共 3 个, 共 10 个
        r"红包.*?(\d+)\s*个",  # 红包3个
        r"数量[：:]\s*(\d+)",  # 数量: 3
        r"共\s*(\d+)\s*份",  # 共3份
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                count = int(match.group(1))
                if count > 0:
                    return count
            except (ValueError, IndexError):
                continue

    return None


def split_multiple_red_packets(text: str) -> List[str]:
    """
    分割多条红包的消息，返回单个红包块的列表
    支持的分隔符：
    - ➖➖➖➖➖➖➖➖➖➖
    - ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
    - ========
    - -------
    """
    if not text:
        return []

    # 按分隔符分割
    separators = [
        r"➖{5,}",  # ➖➖➖➖➖➖➖➖➖➖
        r"─{5,}",   # ────────────
        r"={5,}",   # ===========
        r"-{5,}",   # ----------
    ]

    blocks = [text]
    for sep in separators:
        new_blocks = []
        for block in blocks:
            parts = re.split(sep, block)
            new_blocks.extend([p.strip() for p in parts if p.strip()])
        blocks = new_blocks

    # 过滤掉不含红包关键字的块
    valid_blocks = []
    for block in blocks:
        block_clean = block.strip()
        # 检查是否包含红包相关关键字
        if any(keyword in block_clean for keyword in ["口令", "🔑", "红包", "🧧", "编号", "🆔", "总额"]):
            valid_blocks.append(block_clean)

    return valid_blocks


def extract_keyword_from_block(block: str) -> Optional[tuple[str, str]]:
    """
    从单个红包块中提取口令
    支持格式：
    - 🔑 口令: 10
    - 口令: 10
    - 口令：10
    返回: (口令, 类型) 或 None
    """
    if not block:
        return None

    # 匹配口令格式
    patterns = [
        (r"🔑\s*口令[：:]\s*(.+?)(?:\n|│|$)", "红包口令-emoji"),
        (r"口令[：:]\s*(.+?)(?:\n|│|$)", "红包口令"),
    ]

    for pattern, keyword_type in patterns:
        match = re.search(pattern, block)
        if match:
            keyword = match.group(1).strip()
            # 清理口令中的引号和多余空格
            keyword = keyword.strip('"\'「」【】 \n')
            # 过滤掉明显的分隔符或无用字符
            if keyword and keyword not in ["➖", "─", "=", "-"] and len(keyword) <= 50:
                return (keyword, keyword_type)

    return None


def check_red_packet_finished(text: str, chat_id: int, is_test: bool) -> bool:
    """
    检查红包/抽奖是否已结束，如果是则清除该口令记录
    返回: 是否处理了这个消息
    """
    # 红包已领完的模式
    finished_patterns = [
        r"已领完",              # 🧧 拼手气红包[xxx]已领完！
        r"已领取完毕",
        r"红包已被领完",
        r"领取详情:",
        r"中奖信息",             # 抽奖开奖，显示中奖者信息
        r"参与人数够啦.*开奖",   # 参与人数够啦！！开奖~
    ]
    
    for pattern in finished_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            # 提取红包口令（如果有）
            result = KeywordExtractor.extract(text)
            extracted_keyword = result[0] if result else None
            
            # 清除该群的所有 pending_draws（抽奖结束了）
            cleared_pending = []
            for pq_key in list(pending_draws.keys()):
                if pq_key.startswith(f"{chat_id}_"):
                    pending_info = pending_draws[pq_key]
                    cleared_pending.append(pending_info.get("keyword", "unknown"))
                    del pending_draws[pq_key]
            if cleared_pending:
                logs.info(f"[LuckyDraw] 抽奖已结束，清除待处理队列: {cleared_pending}")
            
            # 清除口令记录（以便下次相同口令能再次发送）
            if extracted_keyword:
                # 清除指定口令
                key = str(chat_id)
                if key in config.sent_keywords and extracted_keyword in config.sent_keywords[key]:
                    config.sent_keywords[key].remove(extracted_keyword)
                # 同步清除待刷新的变更，防止 flush 时被重新合并回来
                if key in config._pending_keyword_changes and extracted_keyword in config._pending_keyword_changes[key]:
                    config._pending_keyword_changes[key].remove(extracted_keyword)
                config.save()
                logs.info(f"[LuckyDraw] 抽奖已结束，清除口令记录: {extracted_keyword}")
                if is_test:
                    return True
            else:
                # 如果没有提取到口令，清除该群所有口令（保守处理）
                key = str(chat_id)
                removed = []
                if key in config.sent_keywords and config.sent_keywords[key]:
                    removed = config.sent_keywords.pop(key)
                # 同步清除待刷新的变更，防止 flush 时被重新合并回来
                if key in config._pending_keyword_changes:
                    config._pending_keyword_changes.pop(key)
                config.save()
                if removed:
                    logs.info(f"[LuckyDraw] 抽奖已结束，清除该群所有口令记录: {removed}")
                if is_test:
                    logs.info(f"[LuckyDraw] 检测到抽奖已结束，已清除该群所有口令记录")
            
            return True
    
    return False


def is_lottery_bot_message(text: str) -> bool:
    """
    检测消息是否是抽奖机器人的消息格式
    抽奖机器人使用关键词匹配，只要消息包含关键词即可参与
    所以对于这种格式的消息，应该直接转发全文
    """
    if not text:
        return False
    
    # 检测是否包含抽奖机器人消息的关键字段
    lottery_bot_markers = [
        "抽奖 ID：",
        "参与关键词：",
        "自动开奖人数：",
    ]
    
    # 需要至少包含"抽奖 ID："和"参与关键词："才认为是抽奖机器人消息
    has_lottery_id = "抽奖 ID：" in text
    has_keyword = "参与关键词：" in text
    
    return has_lottery_id and has_keyword


class KeywordExtractor:
    """口令提取器"""

    # 口令提取规则列表（按优先级排序）
    PATTERNS = [
        # 格式1: 【密令抽奖】...领取密令: xxx
        (r"领取密令[：:]\s*(.+?)(?:\n|$)", "密令抽奖"),
        # 格式2: 参与关键词：「xxx」 或 参与关键词：xxx
        (r"参与关键词[：:]\s*[「「\"]?(.+?)[」」\"]?(?:\n|$)", "参与关键词"),
        # 格式3: 发送 xxx 进行领取（口令在中间，优先级最高）
        (r"发送\s+(.+?)\s+进行领取", "红包口令-进行领取"),
        # 格式3.5: 发送 xxx 领取 / 发送下方口令领取：xxx（口令在领取之后）
        (r"发送.+(?:领取)[：:]?\s*(.+?)(?:\n|$)", "红包口令"),
        # 格式4: 输入口令: xxx / 口令: xxx
        (r"(?:输入)?口令[：:]\s*(.+?)(?:\n|$)", "口令"),
        # 格式5: 回复 xxx 领取 / 回复 xxx 参与
        (r"回复\s+(.+?)\s+(?:领取|参与)", "回复口令"),
        # 格式6: 【拼手气红包】xxx (红包ID)
        (r"【拼手气红包】\s*([a-zA-Z0-9\-]+)(?:\s|$)", "拼手气红包"),
    ]

    @classmethod
    def extract(cls, text: str) -> Optional[tuple[str, str]]:
        """
        从消息文本中提取口令
        返回: (口令内容, 口令类型) 或 None
        """
        if not text:
            return None

        for pattern, keyword_type in cls.PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                keyword = match.group(1).strip()
                # 清理口令中的引号和多余空格
                keyword = keyword.strip('"\'「」【】')
                if keyword and len(keyword) > 0:
                    # 忽略以 / 开头的命令类关键词（如 /mysterybox）
                    if keyword.startswith('/'):
                        return None
                    return (keyword, keyword_type)

        return None


class SecurityChecker:
    """安全检测器"""

    @classmethod
    def is_safe(cls, text: str, keyword: str) -> tuple[bool, str]:
        """
        检查消息和口令是否安全
        返回: (是否安全, 原因)
        """
        check_text = f"{text} {keyword}".lower()

        for danger_word in SCRIPT_DETECTION_KEYWORDS:
            if danger_word.lower() in check_text:
                return False, f"检测到敏感词: {danger_word}"

        return True, "安全"


# ==================== 生命周期钩子 ====================


@Hook.on_startup()
async def luckydraw_startup():
    """插件启动时执行"""
    logs.info("[LuckyDraw] 自动抽奖插件已加载")


@Hook.on_shutdown()
async def luckydraw_shutdown():
    """插件关闭时执行"""
    # 关闭前确保所有待刷新的数据写入磁盘
    if config._pending_save:
        await config._flush_to_disk()
    logs.info("[LuckyDraw] 自动抽奖插件已卸载")


# ==================== 管理命令 ====================


@listener(
    command="ldraw",
    description="自动抽奖管理命令",
    parameters="<on|off|set|list|stats|help>",
    is_plugin=True,
)
async def ldraw_command(message: Message):
    """处理 LuckyDraw 管理命令"""
    # 获取命令参数
    text = message.arguments or ""

    # 如果没有参数，显示帮助
    if not text or text.strip() == "":
        await show_help(message)
        return

    cmd = text.lower().split()[0]

    if cmd == "on":
        await enable_chat(message)
    elif cmd == "off":
        await disable_chat(message)
    elif cmd == "set":
        await set_chat(message)
    elif cmd == "delayset":
        await set_delay_by_chat_id(message)
    elif cmd == "delayoff":
        await remove_delay_by_chat_id(message)
    elif cmd == "delay":
        await set_delay(message)
    elif cmd == "listdelay":
        await list_delays(message)
    elif cmd == "list":
        await list_chats(message)
    elif cmd == "stats":
        await show_stats(message)
    elif cmd == "help":
        await show_help(message)
    elif cmd == "test":
        await test_extract(message)
    elif cmd == "clear":
        await clear_keywords(message)
    elif cmd == "bot":
        await manage_bot(message)
    elif cmd == "sticker":
        await manage_sticker(message)
    else:
        await show_help(message)


async def show_help(message: Message):
    """显示帮助信息"""
    help_text = """**自动抽奖插件使用说明**

**功能描述：**
在启用功能的群组中，自动识别抽奖活动消息并发送口令参与。
每个口令只发送一次，不会重复发送。

**⚠️ 重要：机器人白名单机制**
只响应白名单中机器人发布的抽奖消息，忽略其他用户发布的抽奖。

**管理命令：**

`,ldraw bot list` - 查看白名单机器人
`,ldraw bot add <ID>` - 添加机器人到白名单
`,ldraw bot del <ID>` - 从白名单移除机器人

`,ldraw set <群组ID>` - 启用指定群组
`,ldraw set <群组ID> off` - 禁用指定群组
`,ldraw set <群组ID> test` - 添加到测试群组（输出详细日志）

`,ldraw delay <延时>` - 设置当前群组延时
`,ldraw delay <最小> <最大>` - 设置精确延时范围
`,ldraw delayset <群组ID> <最小延时> [最大延时]` - 设置指定群组延时
`,ldraw delayoff <群组ID>` - 移除指定群组延时
`,ldraw listdelay` - 查看所有群组延时配置
`,ldraw list` - 查看所有启用的群组
`,ldraw stats` - 查看统计信息
`,ldraw test <文本>` - 测试口令提取功能
`,ldraw clear` - 清除已发送口令记录

**庆祝贴纸：**
`,ldraw sticker list` - 查看庆祝贴纸列表
`,ldraw sticker add` - 回复贴纸添加到列表（中奖后自动发送）
`,ldraw sticker del` - 回复贴纸从列表移除

**支持的口令格式：**
- `领取密令: xxx` → 发送 xxx
- `参与关键词：「xxx」` → 发送 xxx
- `口令: xxx` → 发送 xxx

**注意事项：**
- 红包个数 < 5：直接发送关键词（抢小红包优先速度）
- 红包个数 >= 5：转发群里首个回复关键词的用户消息（伪装成真人）
- 如需延时防护，可用 `,ldraw delay` 命令设置
- 测试群组会输出详细日志方便调试"""

    await message.edit(help_text)


async def enable_chat(message: Message):
    """在当前群组启用功能"""
    if not message.chat or message.chat.id > 0:
        await message.edit("此命令只能在群组中使用")
        await asyncio.sleep(3)
        await message.delete()
        return

    chat_id = message.chat.id
    result = config.add_chat(chat_id)
    await message.edit(f"**{result}**")
    await asyncio.sleep(3)
    await message.delete()


async def disable_chat(message: Message):
    """在当前群组禁用功能"""
    if not message.chat or message.chat.id > 0:
        await message.edit("此命令只能在群组中使用")
        await asyncio.sleep(3)
        await message.delete()
        return

    chat_id = message.chat.id
    result = config.remove_chat(chat_id)
    await message.edit(f"**{result}**")
    await asyncio.sleep(3)
    await message.delete()


async def set_chat(message: Message):
    """手动添加/移除群组ID"""
    params = message.arguments.split()
    if len(params) < 2:
        await message.edit(
            "**参数错误！**\n\n"
            "使用方法: \n"
            "`,ldraw set <群组ID>` - 启用指定群组\n"
            "`,ldraw set <群组ID> off` - 禁用指定群组\n"
            "`,ldraw set <群组ID> test` - 添加到测试群组\n"
            "`,ldraw set <群组ID> test off` - 从测试群组移除\n\n"
            "示例: `,ldraw set -1001234567890`"
        )
        await asyncio.sleep(8)
        await message.delete()
        return

    try:
        chat_id = int(params[1])
        
        # 判断操作类型
        if len(params) >= 3:
            action = params[2].lower()
            if action == "off":
                result = config.remove_chat(chat_id)
            elif action == "test":
                if len(params) >= 4 and params[3].lower() == "off":
                    result = config.remove_test_chat(chat_id)
                else:
                    result = config.add_test_chat(chat_id)
            else:
                result = config.add_chat(chat_id)
        else:
            result = config.add_chat(chat_id)
        
        await message.edit(f"**{result}**")
        await asyncio.sleep(3)
        await message.delete()
    except ValueError:
        await message.edit("**群组ID格式错误！**\n\n请输入有效的数字ID")
        await asyncio.sleep(3)
        await message.delete()


async def list_chats(message: Message):
    """查看所有启用的群组"""
    result = config.list_chats()
    await message.edit(result)
    await asyncio.sleep(5)
    await message.delete()


async def show_stats(message: Message):
    """查看统计信息"""
    result = config.get_stats()
    await message.edit(result)
    await asyncio.sleep(5)
    await message.delete()


async def test_extract(message: Message):
    """测试口令提取功能"""
    params = message.arguments.split(maxsplit=1)
    if len(params) < 2:
        await message.edit(
            "**参数错误！**\n\n"
            "使用方法:\n"
            "`,ldraw test <文本>` - 测试单条口令提取\n"
            "`,ldraw test multi <文本>` - 测试多条红包提取\n\n"
            "示例:\n"
            "`,ldraw test 领取密令: 新年快乐`\n"
            "`,ldraw test multi [粘贴多条红包消息]`"
        )
        await asyncio.sleep(5)
        await message.delete()
        return

    test_text = params[1]

    # 检查是否是 multi 模式
    if test_text.lower().startswith("multi "):
        test_text = test_text[6:]  # 去掉 "multi " 前缀
        await test_multi_extract(message, test_text)
        return

    result = KeywordExtractor.extract(test_text)

    if result:
        keyword, keyword_type = result
        is_safe, reason = SecurityChecker.is_safe(test_text, keyword)
        status = "安全" if is_safe else f"不安全 ({reason})"

        output = (
            f"**口令提取测试结果：**\n\n"
            f"- 口令类型: `{keyword_type}`\n"
            f"- 提取结果: `{keyword}`\n"
            f"- 安全检测: {status}"
        )
    else:
        output = "**未能从文本中提取到口令**\n\n请检查文本格式是否符合支持的口令格式。"

    await message.edit(output)
    await asyncio.sleep(5)
    await message.delete()


async def test_multi_extract(message: Message, test_text: str):
    """测试多条红包口令提取"""
    # 分割红包
    blocks = split_multiple_red_packets(test_text)

    if not blocks or len(blocks) <= 1:
        # 如果分割不出多个红包，退回到单条提取
        result = KeywordExtractor.extract(test_text)
        if result:
            keyword, keyword_type = result
            output = (
                f"**单条口令提取结果：**\n\n"
                f"- 口令类型: `{keyword_type}`\n"
                f"- 提取结果: `{keyword}`"
            )
        else:
            output = "**未能从文本中提取到口令**\n\n无法识别多条红包格式。"
        await message.edit(output)
        await asyncio.sleep(5)
        await message.delete()
        return

    output = f"**多条红包提取测试结果：**\n\n检测到 **{len(blocks)}** 个红包\n\n"
    valid_count = 0

    for i, block in enumerate(blocks):
        # 提取单个红包的口令
        block_result = extract_keyword_from_block(block)
        # 提取剩余个数
        red_packet_count = extract_red_packet_count(block)

        if block_result:
            keyword, keyword_type = block_result
            count_info = f"剩余{red_packet_count}个" if red_packet_count else "个数未知"
            mode = "转发" if (red_packet_count and red_packet_count >= 5) else "直发"

            output += f"{i+1}. `{keyword}` | {count_info} | {mode}\n"
            valid_count += 1
        else:
            output += f"{i+1}. ❌ 无法提取口令\n"

    output += f"\n✅ 成功提取 {valid_count}/{len(blocks)} 个口令"

    await message.edit(output)
    await asyncio.sleep(8)
    await message.delete()


async def clear_keywords(message: Message):
    """清除已发送口令记录"""
    params = message.arguments.split()
    if len(params) >= 2:
        try:
            chat_id = int(params[1])
            result = config.clear_sent_keywords(chat_id)
        except ValueError:
            await message.edit("**群组ID格式错误！**\n\n请输入有效的数字ID")
            await asyncio.sleep(3)
            await message.delete()
            return
    else:
        result = config.clear_sent_keywords()
    
    await message.edit(f"**{result}**")
    await asyncio.sleep(3)
    await message.delete()


async def manage_bot(message: Message):
    """管理机器人白名单"""
    params = message.arguments.split()
    
    if len(params) < 2:
        await message.edit(
            "**参数错误！**\n\n"
            "使用方法:\n"
            "`,ldraw bot list` - 查看白名单机器人列表\n"
            "`,ldraw bot add <机器人ID>` - 添加机器人到白名单\n"
            "`,ldraw bot del <机器人ID>` - 从白名单移除机器人\n\n"
            "示例:\n"
            "`,ldraw bot add 6461022460`\n"
            "`,ldraw bot del 6461022460`"
        )
        await asyncio.sleep(8)
        await message.delete()
        return
    
    action = params[1].lower()
    
    if action == "list":
        result = config.list_bots()
        await message.edit(result)
        await asyncio.sleep(5)
        await message.delete()
        return
    
    if len(params) < 3:
        await message.edit("**参数错误！**\n\n需要指定机器人ID")
        await asyncio.sleep(3)
        await message.delete()
        return
    
    try:
        bot_id = int(params[2])
    except ValueError:
        await message.edit("**机器人ID格式错误！**\n\n请输入有效的数字ID")
        await asyncio.sleep(3)
        await message.delete()
        return
    
    if action == "add":
        result = config.add_bot(bot_id)
    elif action in ["del", "remove", "delete"]:
        result = config.remove_bot(bot_id)
    else:
        await message.edit("**未知操作！**\n\n支持的操作: add, del")
        await asyncio.sleep(3)
        await message.delete()
        return
    
    await message.edit(f"**{result}**")
    await asyncio.sleep(3)
    await message.delete()


async def manage_sticker(message: Message):
    """管理庆祝贴纸"""
    params = message.arguments.split()

    if len(params) < 2:
        await message.edit(
            "**庆祝贴纸管理**\n\n"
            "使用方法:\n"
            "`,ldraw sticker list` - 查看庆祝贴纸列表\n"
            "`,ldraw sticker add` - 回复贴纸添加到列表\n"
            "`,ldraw sticker del <序号>` - 按序号删除贴纸\n"
            "`,ldraw sticker del` - 回复贴纸从列表移除\n"
            "`,ldraw sticker clear` - 清空所有贴纸\n\n"
            "💡 中奖后会随机发送一个贴纸庆祝"
        )
        await asyncio.sleep(8)
        await message.delete()
        return

    action = params[1].lower()

    if action == "list":
        result = config.list_stickers()
        await message.edit(result)
        await asyncio.sleep(5)
        await message.delete()
        return

    if action == "clear":
        config.celebration_stickers.clear()
        config.save()
        await message.edit("**已清空所有庆祝贴纸**")
        await asyncio.sleep(3)
        await message.delete()
        return

    if action in ["del", "remove", "delete"]:
        # 支持按序号删除: `,ldraw sticker del 1`
        if len(params) >= 3:
            try:
                index = int(params[2])
                sticker_list = list(config.celebration_stickers)
                if index < 1 or index > len(sticker_list):
                    await message.edit(f"**序号超出范围！**\n\n当前共有 {len(sticker_list)} 个贴纸")
                    await asyncio.sleep(3)
                    await message.delete()
                    return
                target_id = sticker_list[index - 1]
                result = config.remove_sticker(target_id)
                await message.edit(f"**{result}**")
                await asyncio.sleep(3)
                await message.delete()
                return
            except ValueError:
                await message.edit("**序号格式错误！**\n\n请输入有效的数字序号")
                await asyncio.sleep(3)
                await message.delete()
                return

    # add 操作需要回复贴纸
    if action == "add":
        if not message.reply_to_message:
            await message.edit("**请回复一个贴纸消息来添加！**")
            await asyncio.sleep(3)
            await message.delete()
            return

        reply_msg = message.reply_to_message
        sticker = getattr(reply_msg, 'sticker', None)

        if not sticker:
            await message.edit("**回复的消息不是贴纸！**")
            await asyncio.sleep(3)
            await message.delete()
            return

        file_id = getattr(sticker, 'file_id', None)
        if not file_id:
            await message.edit("**无法获取贴纸ID！**")
            await asyncio.sleep(3)
            await message.delete()
            return

        result = config.add_sticker(file_id)
        await message.edit(f"**{result}**")
        await asyncio.sleep(3)
        await message.delete()
        return

    await message.edit("**未知操作！**\n\n支持的操作: list, add, del, clear")
    await asyncio.sleep(3)
    await message.delete()


async def set_delay(message: Message):
    """设置群组的延时"""
    params = message.arguments.split()
    
    if len(params) < 2:
        await message.edit(
            "**参数错误！**\n\n"
            "使用方法:\n"
            "`,ldraw delay <延时秒数>` - 设置当前群组延时（最小~最大范围自动+3秒）\n"
            "`,ldraw delay <最小延时> <最大延时>` - 设置精确范围\n"
            "`,ldraw delay <群组ID> <延时>` - 设置指定群组延时\n"
            "`,ldraw delay off` - 移除当前群组的自定义延时\n\n"
            "示例:\n"
            "`,ldraw delay 0.5` - 设置延时 0.5~3.5 秒\n"
            "`,ldraw delay 2 5` - 设置延时 2~5 秒\n"
            "`,ldraw delay -1001234567890 3` - 设置指定群组延时 3~6 秒"
        )
        await asyncio.sleep(10)
        await message.delete()
        return
    
    # 判断是设置还是移除
    if params[1].lower() == "off":
        # 移除当前群组的延时配置
        if not message.chat or message.chat.id > 0:
            await message.edit("此命令只能在群组中使用")
            await asyncio.sleep(3)
            await message.delete()
            return
        chat_id = message.chat.id
        result = config.remove_chat_delay(chat_id)
        await message.edit(f"**{result}**")
        await asyncio.sleep(3)
        await message.delete()
        return
    
    # 解析参数
    try:
        # 判断第一个参数是否是群组ID（负数）
        if params[1].startswith("-"):
            if len(params) < 3:
                await message.edit("**参数错误！**\n\n需要指定延时值")
                await asyncio.sleep(3)
                await message.delete()
                return
            chat_id = int(params[1])
            min_delay = float(params[2])
            max_delay = float(params[3]) if len(params) > 3 else None
        else:
            # 当前群组
            if not message.chat or message.chat.id > 0:
                await message.edit("此命令只能在群组中使用")
                await asyncio.sleep(3)
                await message.delete()
                return
            chat_id = message.chat.id
            min_delay = float(params[1])
            max_delay = float(params[2]) if len(params) > 2 else None
        
        result = config.set_chat_delay(chat_id, min_delay, max_delay)
        await message.edit(f"**{result}**")
        await asyncio.sleep(3)
        await message.delete()
    except ValueError:
        await message.edit("**参数错误！**\n\n请输入有效的数字")
        await asyncio.sleep(3)
        await message.delete()


async def list_delays(message: Message):
    """列出所有群组的延时配置"""
    result = config.list_chat_delays()
    await message.edit(result)
    await asyncio.sleep(5)
    await message.delete()


async def set_delay_by_chat_id(message: Message) -> None:
    """通过群组ID设置延时"""
    params = message.arguments.split()

    if len(params) < 3:
        await message.edit(
            "**参数错误！**\n\n"
            "使用方法:\n"
            "`,ldraw delayset <群组ID> <最小延时> [最大延时]`\n\n"
            "示例:\n"
            "`,ldraw delayset -1001234567890 0.5`\n"
            "`,ldraw delayset -1001234567890 2 5`"
        )
        await asyncio.sleep(8)
        await message.delete()
        return

    try:
        chat_id = int(params[1])
    except ValueError:
        await message.edit("**群组ID格式错误！**\n\n请输入有效的数字ID")
        await asyncio.sleep(3)
        await message.delete()
        return

    if chat_id >= 0:
        await message.edit("**群组ID错误！**\n\n群组ID必须为负数")
        await asyncio.sleep(3)
        await message.delete()
        return

    try:
        min_delay = float(params[2])
    except ValueError:
        await message.edit("**参数错误！**\n\n最小延时必须为数字")
        await asyncio.sleep(3)
        await message.delete()
        return

    max_delay: Optional[float] = None
    if len(params) >= 4:
        try:
            max_delay = float(params[3])
        except ValueError:
            await message.edit("**参数错误！**\n\n最大延时必须为数字")
            await asyncio.sleep(3)
            await message.delete()
            return

    if min_delay < 0.1:
        await message.edit("**参数错误！**\n\n最小延时不能小于 0.1 秒")
        await asyncio.sleep(3)
        await message.delete()
        return

    if max_delay is not None and max_delay < min_delay:
        await message.edit("**参数错误！**\n\n最大延时必须大于等于最小延时")
        await asyncio.sleep(3)
        await message.delete()
        return

    result = config.set_chat_delay(chat_id, min_delay, max_delay)
    await message.edit(f"**{result}**")
    await asyncio.sleep(3)
    await message.delete()


async def remove_delay_by_chat_id(message: Message) -> None:
    """通过群组ID移除延时"""
    params = message.arguments.split()

    if len(params) < 2:
        await message.edit(
            "**参数错误！**\n\n"
            "使用方法:\n"
            "`,ldraw delayoff <群组ID>`\n\n"
            "示例:\n"
            "`,ldraw delayoff -1001234567890`"
        )
        await asyncio.sleep(6)
        await message.delete()
        return

    try:
        chat_id = int(params[1])
    except ValueError:
        await message.edit("**群组ID格式错误！**\n\n请输入有效的数字ID")
        await asyncio.sleep(3)
        await message.delete()
        return

    if chat_id >= 0:
        await message.edit("**群组ID错误！**\n\n群组ID必须为负数")
        await asyncio.sleep(3)
        await message.delete()
        return

    result = config.remove_chat_delay(chat_id)
    await message.edit(f"**{result}**")
    await asyncio.sleep(3)
    await message.delete()


# ==================== 自动抽奖监听器 ====================


@listener(is_plugin=True, incoming=True, outgoing=False, ignore_edited=False)
async def luckydraw_handler(message: Message, bot: Client):
    """
    自动抽奖消息处理器

    检测传入的消息，识别红包/抽奖活动并自动发送口令参与
    """
    # 检查是否在群组中
    if not message.chat:
        return

    chat_id = message.chat.id
    is_test = config.is_test_chat(chat_id)

    # 检查是否在启用的群组中
    if not config.is_enabled(chat_id):
        if is_test:
            logs.info(f"[LuckyDraw] 群组 {chat_id} 未启用，跳过")
        return

    # 打印所有消息属性用于调试
    if is_test:
        logs.info(f"[LuckyDraw] ===== 收到消息 =====")
        logs.info(f"[LuckyDraw] message.text: {message.text}")
        logs.info(f"[LuckyDraw] message.caption: {getattr(message, 'caption', 'N/A')}")
        logs.info(f"[LuckyDraw] message.raw_text: {getattr(message, 'raw_text', 'N/A')}")
        logs.info(f"[LuckyDraw] message.forward_from: {message.forward_from}")
        logs.info(f"[LuckyDraw] message.forward_from_chat: {message.forward_from_chat}")
        logs.info(f"[LuckyDraw] message.media: {getattr(message, 'media', 'N/A')}")
        logs.info(f"[LuckyDraw] message.chat.id: {chat_id}")
        logs.info(f"[LuckyDraw] message.sender_id: {getattr(message, 'sender_id', 'N/A')}")

    # ========== 机器人ID检测 ==========
    # 只处理白名单中机器人发布的抽奖消息
    
    # 获取发送者ID（参考 userinfo 插件）
    sender_id = None
    
    # 判断是否是频道/群组发言（皮套）
    if hasattr(message, 'sender_chat') and message.sender_chat:
        sender_id = message.sender_chat.id
    # 普通用户/机器人发言
    elif hasattr(message, 'from_user') and message.from_user:
        sender_id = message.from_user.id
    
    # 检查是否是转发的消息（转发消息需要检查原始发送者）
    forward_from = getattr(message, "forward_from", None)
    forward_from_chat = getattr(message, "forward_from_chat", None)
    
    # 确定实际的发送者ID
    actual_sender_id = sender_id
    if forward_from:
        # 转发自用户
        actual_sender_id = getattr(forward_from, "id", sender_id)
    elif forward_from_chat:
        # 转发自频道/群组
        actual_sender_id = getattr(forward_from_chat, "id", sender_id)
    
    if is_test:
        logs.info(f"[LuckyDraw] sender_chat: {getattr(message, 'sender_chat', None)}")
        logs.info(f"[LuckyDraw] from_user: {getattr(message, 'from_user', None)}")
        logs.info(f"[LuckyDraw] sender_id: {sender_id}, actual_sender_id: {actual_sender_id}")
    
    # 检查发送者是否在白名单中
    if not config.is_bot_allowed(actual_sender_id):
        if is_test:
            logs.info(f"[LuckyDraw] 发送者 {actual_sender_id} 不在白名单中，跳过")
        return
    
    if is_test:
        logs.info(f"[LuckyDraw] 发送者 {actual_sender_id} 在白名单中，继续处理")

    # 尝试获取消息文本（支持转发消息和媒体消息）
    text = message.text
    if not text:
        # 尝试获取 media 的 caption
        text = getattr(message, 'caption', None)
    if not text:
        text = getattr(message, 'raw_text', None)
    
    if is_test:
        logs.info(f"[LuckyDraw] 最终获取的text: {text[:100] if text else 'None'}...")

    # 检查是否有消息文本
    if not text:
        if is_test:
            logs.info(f"[LuckyDraw] 无法获取消息文本，跳过")
        return


    # 检查消息是否包含自排除关键词，如果是则不参与抽奖
    text_lower = text.lower()
    for exclude_keyword in SELF_EXCLUSION_KEYWORDS:
        if exclude_keyword in text_lower:
            if is_test:
                logs.info(f"[LuckyDraw] 检测到自排除关键词 '{exclude_keyword}'，跳过")
            return

    # 检查是否红包已领完，如果是则清除该口令记录
    if check_red_packet_finished(text, chat_id, is_test):
        return

    # 检查消息是否已处理（去重）- 进程内快速检查
    message_id = message.id
    # 先检查进程内缓存（快速路径）
    if message_id in _processed_messages[chat_id]:
        if is_test:
            logs.info(f"[LuckyDraw] 消息已处理过(进程内)，跳过 | message_id: {message_id}")
        return
    # 再检查磁盘（慢速路径）
    if config.is_message_processed(chat_id, message_id):
        _processed_messages[chat_id].add(str(message_id))  # 记录到进程内缓存
        if is_test:
            logs.info(f"[LuckyDraw] 消息已处理过(磁盘)，跳过 | message_id: {message_id}")
        return
    # 标记已处理
    config.mark_message_processed(chat_id, message_id)
    _processed_messages[chat_id].add(str(message_id))
    # 限制进程内缓存大小，防止内存泄漏
    if len(_processed_messages[chat_id]) > 500:
        # 只保留最近的200条
        _processed_messages[chat_id] = set(list(_processed_messages[chat_id])[-200:])

    # ========== 检查是否包含多条红包 ==========
    red_packet_blocks = split_multiple_red_packets(text)

    if is_test:
        if red_packet_blocks:
            logs.info(f"[LuckyDraw] 检测到多条红包，共 {len(red_packet_blocks)} 个")
        else:
            logs.info(f"[LuckyDraw] 单条红包消息")

    if red_packet_blocks and len(red_packet_blocks) > 1:
        # ========== 多条红包：逐个处理每个红包 ==========
        # 标记消息已处理（防止重复）
        # 实际上每个红包是独立的，这里不需要标记整条消息

        processed_keywords = set()  # 记录本消息中已处理的口令（避免重复）

        for i, block in enumerate(red_packet_blocks):
            # 从单个红包块提取口令
            block_result = extract_keyword_from_block(block)
            if not block_result:
                if is_test:
                    logs.info(f"[LuckyDraw] 红包块 {i+1}: 无法提取口令")
                continue

            keyword, keyword_type = block_result

            # 检查是否已处理过这个口令
            if keyword in processed_keywords or config.has_sent_keyword(chat_id, keyword):
                if is_test:
                    logs.info(f"[LuckyDraw] 红包块 {i+1}: 口令已发送过，跳过 | {keyword}")
                continue

            processed_keywords.add(keyword)

            # 检查是否红包已领完
            if check_red_packet_finished(block, chat_id, is_test):
                if is_test:
                    logs.info(f"[LuckyDraw] 红包块 {i+1}: 红包已领完，跳过 | {keyword}")
                continue

            # 提取单个红包的剩余个数
            red_packet_count = extract_red_packet_count(block)

            # 判断模式
            use_forward_mode = red_packet_count is not None and red_packet_count >= REDPACKET_COUNT_THRESHOLD

            if is_test:
                count_info = f"剩余{red_packet_count}个" if red_packet_count else "个数未知"
                mode = "转发模式" if use_forward_mode else "直接发送"
                logs.info(f"[LuckyDraw] 红包块 {i+1}: {count_info} | {mode} | 口令: {keyword}")

            # 安全检测
            is_safe, reason = SecurityChecker.is_safe(block, keyword)
            if not is_safe:
                if is_test:
                    logs.info(f"[LuckyDraw] 红包块 {i+1}: 安全拦截 {reason}")
                continue

            if not use_forward_mode:
                # ========== 直接发送关键词 ==========
                # 获取延时配置
                min_delay, max_delay = config.get_chat_delay(chat_id)
                delay = random.uniform(min_delay, max_delay)
                await asyncio.sleep(delay)

                try:
                    await bot.send_message(chat_id, keyword)
                    config.mark_keyword_sent(chat_id, keyword)
                    config.increment_joined()

                    logs.info(
                        f"[LuckyDraw] 多红包-直接发送 | 群组: {chat_id} | "
                        f"口令: {keyword} | 延迟: {delay:.2f}s"
                    )
                except Exception as e:
                    logs.error(f"[LuckyDraw] 多红包-直接发送失败: {e}")
            else:
                # ========== 转发模式 ==========
                queue_key = f"{chat_id}_{message_id}_{i}"
                pending_draws[queue_key] = {
                    "keyword": keyword,
                    "keyword_type": keyword_type,
                    "chat_id": chat_id,
                    "source_message_id": message_id,
                    "block_index": i,
                }

                if is_test:
                    logs.info(f"[LuckyDraw] 红包块 {i+1}: 已加入转发队列 | 口令: {keyword}")

        # 多红包消息处理完成
        return

    # ========== 单条红包消息处理 ==========
    # 提取口令
    result = KeywordExtractor.extract(text)
    if not result:
        if is_test:
            logs.info(f"[LuckyDraw] 未匹配到口令格式")
        return

    keyword, keyword_type = result
    
    # 检查口令是否已发送过
    if config.has_sent_keyword(chat_id, keyword):
        if is_test:
            logs.info(f"[LuckyDraw] 口令已发送过，跳过 | 口令: {keyword}")
        return

    if is_test:
        logs.info(f"[LuckyDraw] 检测到口令 | 类型: {keyword_type} | 口令: {keyword}")

    # 增加检测计数
    config.increment_detected()

    # 安全检测
    is_safe, reason = SecurityChecker.is_safe(text, keyword)
    if not is_safe:
        config.increment_blocked()
        logs.warning(f"[LuckyDraw] 拦截可疑抽奖: {reason}, 口令: {keyword}")
        if is_test:
            try:
                await bot.send_message(chat_id, f"⚠️ 安全拦截: {reason}")
            except Exception:
                pass
        return

    # ========== 抽奖机器人消息特殊处理 ==========
    # 抽奖机器人使用关键词匹配，只要消息包含关键词即可参与
    # 直接转发原文参与抽奖（不需要等待用户回复）
    if is_lottery_bot_message(text):
        if is_test:
            logs.info(f"[LuckyDraw] 检测到抽奖机器人消息，直接转发原文参与 | 口令: {keyword}")
        
        try:
            await bot.forward_messages(chat_id, chat_id, message.id)
            config.mark_keyword_sent(chat_id, keyword)
            config.increment_joined()
            logs.info(f"[LuckyDraw] 成功参与抽奖（转发抽奖机器人原文） | 群组: {chat_id} | 口令: {keyword}")
            
            if is_test:
                try:
                    await bot.send_message(chat_id, f"✅ 直接转发原文参与: {keyword}")
                except Exception:
                    pass
        except Exception as e:
            logs.error(f"[LuckyDraw] 转发抽奖机器人消息失败: {e}")
        return

    # ========== 红包个数判断 ==========
    # 提取红包个数，判断使用哪种参与方式
    red_packet_count = extract_red_packet_count(text)

    if is_test:
        logs.info(f"[LuckyDraw] 解析红包个数: {red_packet_count}")

    # 红包个数 < 阈值 或 无法解析个数：直接发送关键词
    # 红包个数 >= 阈值：等待群里有人回复后再转发
    use_forward_mode = red_packet_count is not None and red_packet_count >= REDPACKET_COUNT_THRESHOLD

    if is_test:
        if use_forward_mode:
            logs.info(f"[LuckyDraw] 红包个数 {red_packet_count} >= {REDPACKET_COUNT_THRESHOLD}，使用转发模式")
        else:
            mode_desc = f"红包个数 {red_packet_count}" if red_packet_count else "无法解析红包个数"
            logs.info(f"[LuckyDraw] {mode_desc} < {REDPACKET_COUNT_THRESHOLD}，直接发送关键词")

    if not use_forward_mode:
        # ========== 直接发送关键词模式 ==========
        # 检查是否已有相同口令在队列中，避免重复发送
        if config.has_sent_keyword(chat_id, keyword):
            if is_test:
                logs.info(f"[LuckyDraw] 口令已发送过，跳过 | 口令: {keyword}")
            return

        # 获取延时配置
        min_delay, max_delay = config.get_chat_delay(chat_id)
        delay = random.uniform(min_delay, max_delay)
        await asyncio.sleep(delay)

        try:
            await bot.send_message(chat_id, keyword)
            config.mark_keyword_sent(chat_id, keyword)
            config.increment_joined()

            logs.info(
                f"[LuckyDraw] 成功参与抽奖（直接发送关键词） | "
                f"群组: {chat_id} | "
                f"类型: {keyword_type} | "
                f"口令: {keyword} | "
                f"延迟: {delay:.2f}s"
            )

            if is_test:
                try:
                    await bot.send_message(chat_id, f"✅ 直接发送关键词: {keyword}")
                except Exception:
                    pass
        except Exception as e:
            logs.error(f"[LuckyDraw] 直接发送关键词失败: {e}")
        return

    # ========== 转发模式：等待群里有人回复后再转发 ==========

    # 检查是否已有相同口令在队列中，避免重复加入
    for existing_key, existing_pending in pending_draws.items():
        if existing_pending.get("keyword") == keyword and existing_pending.get("chat_id") == chat_id:
            if is_test:
                logs.info(f"[LuckyDraw] 相同口令已在等待队列中，跳过 | 口令: {keyword}")
            return

    queue_key = f"{chat_id}_{message_id}"
    pending_draws[queue_key] = {
        "keyword": keyword,
        "keyword_type": keyword_type,
        "chat_id": chat_id,
        "source_message_id": message_id,
    }

    if is_test:
        logs.info(
            f"[LuckyDraw] 已加入等待队列，等待首个回复关键词的用户消息 | "
            f"群组: {chat_id} | 类型: {keyword_type} | 口令: {keyword}"
        )


# ==================== 监听其他用户回复 ====================


@listener(is_plugin=True, incoming=True, outgoing=False, ignore_edited=False)
async def luckydraw_reply_handler(message: Message, bot: Client):
    """
    监听群内后续消息：
    检测到抽奖关键词后，不再自己直接发送，而是转发第一个发送该关键词的用户原消息。
    """
    if not message.chat:
        return

    chat_id = message.chat.id
    is_test = config.is_test_chat(chat_id)

    if not config.is_enabled(chat_id):
        return

    pending_keys = [k for k in pending_draws.keys() if k.startswith(f"{chat_id}_")]
    if not pending_keys:
        return

    # 忽略机器人自己发的消息
    sender = getattr(message, "sender_id", None)
    bot_id = (await bot.get_me()).id
    if sender == bot_id:
        return

    # 提取当前消息文本
    current_text = message.text or getattr(message, "caption", None) or getattr(message, "raw_text", None)
    if not current_text:
        return

    current_text_normalized = normalize_text(current_text)

    for queue_key in list(pending_draws.keys()):
        if not queue_key.startswith(f"{chat_id}_"):
            continue

        pending = pending_draws[queue_key]
        keyword = pending.get("keyword")
        keyword_type = pending.get("keyword_type")
        source_message_id = pending.get("source_message_id")

        if config.has_sent_keyword(chat_id, keyword):
            if is_test:
                logs.info(f"[LuckyDraw] 口令已发送过，移出等待队列 | 口令: {keyword}")
            del pending_draws[queue_key]
            continue

        # 跳过抽奖源消息本身
        if message.id == source_message_id:
            continue

        # 匹配群里后续用户发言：只要消息中包含抽奖关键词，就跟随转发该消息
        keyword_normalized = normalize_text(keyword)
        is_keyword_matched = keyword_normalized in current_text_normalized

        if not is_keyword_matched:
            continue

        # 获取群组+关键词级别的锁，防止并发重复发送
        lock_key = (chat_id, keyword)
        if lock_key not in keyword_locks:
            keyword_locks[lock_key] = asyncio.Lock()
        lock = keyword_locks[lock_key]

        # 获取群组延时配置
        min_delay, max_delay = config.get_chat_delay(chat_id)
        delay = random.uniform(min_delay, max_delay)
        await asyncio.sleep(delay)

        # 使用锁保护整个检查-转发-标记过程，确保原子性
        async with lock:
            # 二次检查，避免并发重复发送
            if config.has_sent_keyword(chat_id, keyword):
                if queue_key in pending_draws:
                    del pending_draws[queue_key]
                continue

            try:
                await bot.forward_messages(chat_id, chat_id, message.id)
                config.mark_keyword_sent(chat_id, keyword)
                config.increment_joined()

                logs.info(
                    f"[LuckyDraw] 成功参与抽奖（转发首个包含关键词的用户消息） | "
                    f"群组: {chat_id} | "
                    f"类型: {keyword_type} | "
                    f"口令: {keyword} | "
                    f"转发消息ID: {message.id} | "
                    f"延迟: {delay:.2f}s"
                )

                if is_test:
                    try:
                        await bot.send_message(chat_id, f"✅ 已转发首个关键词消息: {keyword}")
                    except Exception:
                        pass
            except Exception as e:
                logs.error(f"[LuckyDraw] 转发关键词消息失败: {e}")
            finally:
                if queue_key in pending_draws:
                    del pending_draws[queue_key]
                # 清理不再需要的锁（口令已发送或处理完成）
                if lock_key in keyword_locks:
                    del keyword_locks[lock_key]


# ==================== 自动点击按钮抽奖 ====================

# 按钮类型抽奖的关键词（按钮文本包含这些词时触发点击）
BUTTON_CLICK_KEYWORDS = [
    "领取",
    "抽奖",
    "参与",
    "抢红包",
    "领取红包",
    "参与抽奖",
    "立即参与",
    "点击领取",
    "马上抢",
]

# 按钮点击随机延迟范围（秒）
BUTTON_CLICK_MIN_DELAY = 1.0
BUTTON_CLICK_MAX_DELAY = 3.0


@listener(is_plugin=True, incoming=True, outgoing=False, ignore_edited=False)
async def luckydraw_button_handler(message: Message, bot: Client):
    """
    自动点击按钮抽奖处理器

    检测带有 inline 按钮的抽奖消息，自动点击"领取红包"等按钮参与抽奖
    """
    # 检查是否在群组中
    if not message.chat:
        return

    chat_id = message.chat.id
    is_test = config.is_test_chat(chat_id)

    # 检查是否在启用的群组中
    if not config.is_enabled(chat_id):
        return

    # 检查消息是否有 inline 键盘
    reply_markup = getattr(message, 'reply_markup', None)
    if not reply_markup:
        return

    # 获取 inline 键盘
    inline_keyboard = getattr(reply_markup, 'inline_keyboard', None)
    if not inline_keyboard:
        return

    # ========== 机器人ID检测 ==========
    sender_id = None
    if hasattr(message, 'sender_chat') and message.sender_chat:
        sender_id = message.sender_chat.id
    elif hasattr(message, 'from_user') and message.from_user:
        sender_id = message.from_user.id

    forward_from = getattr(message, "forward_from", None)
    forward_from_chat = getattr(message, "forward_from_chat", None)

    actual_sender_id = sender_id
    if forward_from:
        actual_sender_id = getattr(forward_from, "id", sender_id)
    elif forward_from_chat:
        actual_sender_id = getattr(forward_from_chat, "id", sender_id)

    # 检查发送者是否在白名单中
    if not config.is_bot_allowed(actual_sender_id):
        if is_test:
            logs.debug(f"[LuckyDraw-Button] 发送者 {actual_sender_id} 不在白名单中，跳过")
        return

    # 检查消息是否已处理
    message_id = message.id
    button_key = f"{chat_id}_{message_id}_button"

    if button_key in _processed_messages[chat_id]:
        if is_test:
            logs.debug(f"[LuckyDraw-Button] 消息已处理过，跳过 | message_id: {message_id}")
        return

    # 遍历所有按钮，查找匹配的抽奖按钮
    target_row = None
    target_col = None
    target_button_text = None

    for row_idx, row in enumerate(inline_keyboard):
        for col_idx, button in enumerate(row):
            button_text = getattr(button, 'text', '')
            if not button_text:
                continue

            # 检查按钮文本是否包含关键词
            for keyword in BUTTON_CLICK_KEYWORDS:
                if keyword in button_text:
                    target_row = row_idx
                    target_col = col_idx
                    target_button_text = button_text
                    break

            if target_row is not None:
                break

        if target_row is not None:
            break

    # 如果没有找到匹配的按钮，跳过
    if target_row is None:
        return

    # 标记消息已处理
    _processed_messages[chat_id].add(button_key)

    # 增加检测计数
    config.increment_detected()

    if is_test:
        logs.info(
            f"[LuckyDraw-Button] 检测到按钮抽奖 | "
            f"群组: {chat_id} | "
            f"按钮: {target_button_text} | "
            f"位置: ({target_row}, {target_col})"
        )

    # 获取群组延时配置
    min_delay, max_delay = config.get_chat_delay(chat_id)
    if min_delay == 0 and max_delay == 0:
        # 如果没有配置延时，使用默认按钮点击延迟
        min_delay = BUTTON_CLICK_MIN_DELAY
        max_delay = BUTTON_CLICK_MAX_DELAY

    delay = random.uniform(min_delay, max_delay)
    await asyncio.sleep(delay)

    try:
        # 点击按钮
        await message.click(target_row, target_col)

        # 标记成功
        config.increment_joined()

        logs.info(
            f"[LuckyDraw-Button] 成功点击抽奖按钮 | "
            f"群组: {chat_id} | "
            f"按钮: {target_button_text} | "
            f"延迟: {delay:.2f}s"
        )

        if is_test:
            try:
                await bot.send_message(chat_id, f"✅ 已点击按钮: {target_button_text}")
            except Exception:
                pass

    except Exception as e:
        logs.error(
            f"[LuckyDraw-Button] 点击按钮失败 | "
            f"群组: {chat_id} | "
            f"按钮: {target_button_text} | "
            f"错误: {e}"
        )


# ==================== 中奖庆祝贴纸 ====================

# 中奖关键词（消息包含这些词时触发庆祝）
WIN_KEYWORDS = [
    "恭喜",
    "中奖",
    "获得",
    "抢到",
    "领到",
    "抽中",
    "获得红包",
    "恭喜你",
]

# 非中奖关键词（包含这些词时不触发）
NOT_WIN_KEYWORDS = [
    "没中",
    "未中",
    "遗憾",
    "可惜",
    "下次",
    "再接再厉",
    "未抽中",
    "没有中",
    "手慢了",
    "已领完",
]

# 中奖庆祝延迟范围（秒）
CELEBRATION_MIN_DELAY = 3.0
CELEBRATION_MAX_DELAY = 5.0


@listener(is_plugin=True, incoming=True, outgoing=False, ignore_edited=False)
async def win_celebration_handler(message: Message, bot: Client):
    """
    中奖庆祝处理器

    检测中奖消息中是否包含自己的 ID，延时 3-5 秒发送随机庆祝贴纸
    """
    # 检查是否在群组中
    if not message.chat:
        return

    chat_id = message.chat.id

    # 检查是否在启用的群组中
    if not config.is_enabled(chat_id):
        return

    # 检查是否有庆祝贴纸
    if not config.celebration_stickers:
        return

    # 获取消息文本
    text = message.text or message.caption or ""
    if not text:
        return

    # 检查是否包含非中奖关键词
    for keyword in NOT_WIN_KEYWORDS:
        if keyword in text:
            return

    # 检查是否包含中奖关键词
    is_win = False
    for keyword in WIN_KEYWORDS:
        if keyword in text:
            is_win = True
            break

    if not is_win:
        return

    # 检查发送者是否在白名单中（只响应抽奖机器人的中奖通知）
    sender_id = None
    if hasattr(message, 'sender_chat') and message.sender_chat:
        sender_id = message.sender_chat.id
    elif hasattr(message, 'from_user') and message.from_user:
        sender_id = message.from_user.id

    if not sender_id or not config.is_bot_allowed(sender_id):
        return

    # ========== 关键：检查消息中是否包含自己的用户 ID ==========
    try:
        me = await bot.get_me()
        my_id = me.id
        # 检查消息文本中是否包含自己的 ID（格式如：(1234567890)）
        if f"({my_id})" not in text:
            # 不是自己中奖，跳过
            return
    except Exception as e:
        logs.debug(f"[LuckyDraw-Celebration] 获取自己ID失败: {e}")
        return

    # 防止重复庆祝
    message_id = message.id
    celebration_key = f"{chat_id}_{message_id}_celebration"

    if celebration_key in _processed_messages[chat_id]:
        return

    _processed_messages[chat_id].add(celebration_key)

    # 获取随机贴纸
    sticker_id = config.get_random_sticker()
    if not sticker_id:
        return

    # 随机延迟 3-5 秒
    delay = random.uniform(CELEBRATION_MIN_DELAY, CELEBRATION_MAX_DELAY)
    await asyncio.sleep(delay)

    try:
        # 发送贴纸
        await bot.send_sticker(chat_id, sticker_id)

        logs.info(
            f"[LuckyDraw-Celebration] 发送庆祝贴纸 | "
            f"群组: {chat_id} | "
            f"贴纸: {sticker_id} | "
            f"延迟: {delay:.2f}s"
        )

    except Exception as e:
        logs.error(
            f"[LuckyDraw-Celebration] 发送贴纸失败 | "
            f"群组: {chat_id} | "
            f"贴纸: {sticker_id} | "
            f"错误: {e}"
        )
