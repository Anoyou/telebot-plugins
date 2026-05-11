"""诗词填空远程插件。

群内抢答模式：bot 发一句缺字古诗 → 群友抢答 → 第一个答对获奖 → 自动下一题。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register

# ─────────────────────────────────────────────────────
# 诗词库：(完整诗句, 作者, 诗题提示)
# ─────────────────────────────────────────────────────
POEMS: list[tuple[str, str, str]] = [
    # ── 唐诗 ──
    ("床前明月光", "李白", "静夜思"),
    ("举头望明月", "李白", "静夜思"),
    ("低头思故乡", "李白", "静夜思"),
    ("春眠不觉晓", "孟浩然", "春晓"),
    ("处处闻啼鸟", "孟浩然", "春晓"),
    ("夜来风雨声", "孟浩然", "春晓"),
    ("花落知多少", "孟浩然", "春晓"),
    ("白日依山尽", "王之涣", "登鹳雀楼"),
    ("黄河入海流", "王之涣", "登鹳雀楼"),
    ("欲穷千里目", "王之涣", "登鹳雀楼"),
    ("更上一层楼", "王之涣", "登鹳雀楼"),
    ("千山鸟飞绝", "柳宗元", "江雪"),
    ("万径人踪灭", "柳宗元", "江雪"),
    ("孤舟蓑笠翁", "柳宗元", "江雪"),
    ("独钓寒江雪", "柳宗元", "江雪"),
    ("锄禾日当午", "李绅", "悯农"),
    ("汗滴禾下土", "李绅", "悯农"),
    ("谁知盘中餐", "李绅", "悯农"),
    ("粒粒皆辛苦", "李绅", "悯农"),
    ("离离原上草", "白居易", "赋得古原草送别"),
    ("一岁一枯荣", "白居易", "赋得古原草送别"),
    ("野火烧不尽", "白居易", "赋得古原草送别"),
    ("春风吹又生", "白居易", "赋得古原草送别"),
    ("松下问童子", "贾岛", "寻隐者不遇"),
    ("言师采药去", "贾岛", "寻隐者不遇"),
    ("只在此山中", "贾岛", "寻隐者不遇"),
    ("云深不知处", "贾岛", "寻隐者不遇"),
    ("红豆生南国", "王维", "相思"),
    ("春来发几枝", "王维", "相思"),
    ("愿君多采撷", "王维", "相思"),
    ("此物最相思", "王维", "相思"),
    ("空山新雨后", "王维", "山居秋暝"),
    ("天气晚来秋", "王维", "山居秋暝"),
    ("明月松间照", "王维", "山居秋暝"),
    ("清泉石上流", "王维", "山居秋暝"),
    ("竹喧归浣女", "王维", "山居秋暝"),
    ("莲动下渔舟", "王维", "山居秋暝"),
    ("独在异乡为异客", "王维", "九月九日忆山东兄弟"),
    ("每逢佳节倍思亲", "王维", "九月九日忆山东兄弟"),
    ("遥知兄弟登高处", "王维", "九月九日忆山东兄弟"),
    ("遍插茱萸少一人", "王维", "九月九日忆山东兄弟"),
    ("秦时明月汉时关", "王昌龄", "出塞"),
    ("万里长征人未还", "王昌龄", "出塞"),
    ("但使龙城飞将在", "王昌龄", "出塞"),
    ("不教胡马度阴山", "王昌龄", "出塞"),
    ("葡萄美酒夜光杯", "王翰", "凉州词"),
    ("欲饮琵琶马上催", "王翰", "凉州词"),
    ("醉卧沙场君莫笑", "王翰", "凉州词"),
    ("古来征战几人回", "王翰", "凉州词"),
    ("两个黄鹂鸣翠柳", "杜甫", "绝句"),
    ("一行白鹭上青天", "杜甫", "绝句"),
    ("窗含西岭千秋雪", "杜甫", "绝句"),
    ("门泊东吴万里船", "杜甫", "绝句"),
    ("好雨知时节", "杜甫", "春夜喜雨"),
    ("当春乃发生", "杜甫", "春夜喜雨"),
    ("随风潜入夜", "杜甫", "春夜喜雨"),
    ("润物细无声", "杜甫", "春夜喜雨"),
    ("国破山河在", "杜甫", "春望"),
    ("城春草木深", "杜甫", "春望"),
    ("感时花溅泪", "杜甫", "春望"),
    ("恨别鸟惊心", "杜甫", "春望"),
    ("故人西辞黄鹤楼", "李白", "黄鹤楼送孟浩然之广陵"),
    ("烟花三月下扬州", "李白", "黄鹤楼送孟浩然之广陵"),
    ("孤帆远影碧空尽", "李白", "黄鹤楼送孟浩然之广陵"),
    ("唯见长江天际流", "李白", "黄鹤楼送孟浩然之广陵"),
    ("朝辞白帝彩云间", "李白", "早发白帝城"),
    ("千里江陵一日还", "李白", "早发白帝城"),
    ("两岸猿声啼不住", "李白", "早发白帝城"),
    ("轻舟已过万重山", "李白", "早发白帝城"),
    ("日照香炉生紫烟", "李白", "望庐山瀑布"),
    ("遥看瀑布挂前川", "李白", "望庐山瀑布"),
    ("飞流直下三千尺", "李白", "望庐山瀑布"),
    ("疑是银河落九天", "李白", "望庐山瀑布"),
    ("月落乌啼霜满天", "张继", "枫桥夜泊"),
    ("江枫渔火对愁眠", "张继", "枫桥夜泊"),
    ("姑苏城外寒山寺", "张继", "枫桥夜泊"),
    ("夜半钟声到客船", "张继", "枫桥夜泊"),
    ("清明时节雨纷纷", "杜牧", "清明"),
    ("路上行人欲断魂", "杜牧", "清明"),
    ("借问酒家何处有", "杜牧", "清明"),
    ("牧童遥指杏花村", "杜牧", "清明"),
    ("远上寒山石径斜", "杜牧", "山行"),
    ("白云深处有人家", "杜牧", "山行"),
    ("停车坐爱枫林晚", "杜牧", "山行"),
    ("霜叶红于二月花", "杜牧", "山行"),
    ("千里莺啼绿映红", "杜牧", "江南春"),
    ("水村山郭酒旗风", "杜牧", "江南春"),
    ("南朝四百八十寺", "杜牧", "江南春"),
    ("多少楼台烟雨中", "杜牧", "江南春"),
    ("大漠孤烟直", "王维", "使至塞上"),
    ("长河落日圆", "王维", "使至塞上"),
    ("慈母手中线", "孟郊", "游子吟"),
    ("游子身上衣", "孟郊", "游子吟"),
    ("临行密密缝", "孟郊", "游子吟"),
    ("意恐迟迟归", "孟郊", "游子吟"),
    ("谁言寸草心", "孟郊", "游子吟"),
    ("报得三春晖", "孟郊", "游子吟"),
    ("海内存知己", "王勃", "送杜少府之任蜀州"),
    ("天涯若比邻", "王勃", "送杜少府之任蜀州"),
    ("鹅鹅鹅", "骆宾王", "咏鹅"),
    ("曲项向天歌", "骆宾王", "咏鹅"),
    ("白毛浮绿水", "骆宾王", "咏鹅"),
    ("红掌拨清波", "骆宾王", "咏鹅"),
    ("返景入深林", "王维", "鹿柴"),
    ("复照青苔上", "王维", "鹿柴"),
    ("危楼高百尺", "李白", "夜宿山寺"),
    ("手可摘星辰", "李白", "夜宿山寺"),
    ("不敢高声语", "李白", "夜宿山寺"),
    ("恐惊天上人", "李白", "夜宿山寺"),
    # ── 宋词 ──
    ("明月几时有", "苏轼", "水调歌头"),
    ("把酒问青天", "苏轼", "水调歌头"),
    ("不知天上宫阙", "苏轼", "水调歌头"),
    ("今夕是何年", "苏轼", "水调歌头"),
    ("人有悲欢离合", "苏轼", "水调歌头"),
    ("月有阴晴圆缺", "苏轼", "水调歌头"),
    ("但愿人长久", "苏轼", "水调歌头"),
    ("千里共婵娟", "苏轼", "水调歌头"),
    ("大江东去", "苏轼", "念奴娇·赤壁怀古"),
    ("浪淘尽", "苏轼", "念奴娇·赤壁怀古"),
    ("千古风流人物", "苏轼", "念奴娇·赤壁怀古"),
    ("故垒西边", "苏轼", "念奴娇·赤壁怀古"),
    ("人生如梦", "苏轼", "念奴娇·赤壁怀古"),
    ("一尊还酹江月", "苏轼", "念奴娇·赤壁怀古"),
    ("寻寻觅觅", "李清照", "声声慢"),
    ("冷冷清清", "李清照", "声声慢"),
    ("凄凄惨惨戚戚", "李清照", "声声慢"),
    ("这次第", "李清照", "声声慢"),
    ("怎一个愁字了得", "李清照", "声声慢"),
    ("昨夜雨疏风骤", "李清照", "如梦令"),
    ("浓睡不消残酒", "李清照", "如梦令"),
    ("试问卷帘人", "李清照", "如梦令"),
    ("却道海棠依旧", "李清照", "如梦令"),
    ("知否知否", "李清照", "如梦令"),
    ("应是绿肥红瘦", "李清照", "如梦令"),
    ("怒发冲冠", "岳飞", "满江红"),
    ("凭栏处", "岳飞", "满江红"),
    ("潇潇雨歇", "岳飞", "满江红"),
    ("抬望眼", "岳飞", "满江红"),
    ("仰天长啸", "岳飞", "满江红"),
    ("壮怀激烈", "岳飞", "满江红"),
    ("三十功名尘与土", "岳飞", "满江红"),
    ("八千里路云和月", "岳飞", "满江红"),
    ("莫等闲", "岳飞", "满江红"),
    ("白了少年头", "岳飞", "满江红"),
    ("空悲切", "岳飞", "满江红"),
    ("靖康耻", "岳飞", "满江红"),
    ("犹未雪", "岳飞", "满江红"),
    ("臣子恨", "岳飞", "满江红"),
    ("何时灭", "岳飞", "满江红"),
    ("壮志饥餐胡虏肉", "岳飞", "满江红"),
    ("笑谈渴饮匈奴血", "岳飞", "满江红"),
    ("十年生死两茫茫", "苏轼", "江城子·乙卯正月二十日夜记梦"),
    ("不思量", "苏轼", "江城子·乙卯正月二十日夜记梦"),
    ("自难忘", "苏轼", "江城子·乙卯正月二十日夜记梦"),
    ("千里孤坟", "苏轼", "江城子·乙卯正月二十日夜记梦"),
    ("无处话凄凉", "苏轼", "江城子·乙卯正月二十日夜记梦"),
    ("老夫聊发少年狂", "苏轼", "江城子·密州出猎"),
    ("左牵黄", "苏轼", "江城子·密州出猎"),
    ("右擎苍", "苏轼", "江城子·密州出猎"),
    ("会挽雕弓如满月", "苏轼", "江城子·密州出猎"),
    ("西北望", "苏轼", "江城子·密州出猎"),
    ("射天狼", "苏轼", "江城子·密州出猎"),
    ("纤云弄巧", "秦观", "鹊桥仙"),
    ("飞星传恨", "秦观", "鹊桥仙"),
    ("银汉迢迢暗度", "秦观", "鹊桥仙"),
    ("金风玉露一相逢", "秦观", "鹊桥仙"),
    ("便胜却人间无数", "秦观", "鹊桥仙"),
    ("柔情似水", "秦观", "鹊桥仙"),
    ("佳期如梦", "秦观", "鹊桥仙"),
    ("忍顾鹊桥归路", "秦观", "鹊桥仙"),
    ("两情若是久长时", "秦观", "鹊桥仙"),
    ("又岂在朝朝暮暮", "秦观", "鹊桥仙"),
    ("春花秋月何时了", "李煜", "虞美人"),
    ("往事知多少", "李煜", "虞美人"),
    ("小楼昨夜又东风", "李煜", "虞美人"),
    ("故国不堪回首月明中", "李煜", "虞美人"),
    ("雕栏玉砌应犹在", "李煜", "虞美人"),
    ("只是朱颜改", "李煜", "虞美人"),
    ("问君能有几多愁", "李煜", "虞美人"),
    ("恰似一江春水向东流", "李煜", "虞美人"),
    ("无言独上西楼", "李煜", "相见欢"),
    ("月如钩", "李煜", "相见欢"),
    ("寂寞梧桐深院锁清秋", "李煜", "相见欢"),
    ("剪不断", "李煜", "相见欢"),
    ("理还乱", "李煜", "相见欢"),
    ("是离愁", "李煜", "相见欢"),
    ("别是一般滋味在心头", "李煜", "相见欢"),
    ("东风夜放花千树", "辛弃疾", "青玉案·元夕"),
    ("更吹落", "辛弃疾", "青玉案·元夕"),
    ("星如雨", "辛弃疾", "青玉案·元夕"),
    ("宝马雕车香满路", "辛弃疾", "青玉案·元夕"),
    ("众里寻他千百度", "辛弃疾", "青玉案·元夕"),
    ("蓦然回首", "辛弃疾", "青玉案·元夕"),
    ("那人却在", "辛弃疾", "青玉案·元夕"),
    ("灯火阑珊处", "辛弃疾", "青玉案·元夕"),
    ("醉里挑灯看剑", "辛弃疾", "破阵子"),
    ("梦回吹角连营", "辛弃疾", "破阵子"),
    ("八百里分麾下炙", "辛弃疾", "破阵子"),
    ("五十弦翻塞外声", "辛弃疾", "破阵子"),
    ("沙场秋点兵", "辛弃疾", "破阵子"),
    ("了却君王天下事", "辛弃疾", "破阵子"),
    ("赢得生前身后名", "辛弃疾", "破阵子"),
    ("可怜白发生", "辛弃疾", "破阵子"),
]


def _blank_line(line: str, count: int = 1) -> tuple[str, list[str]]:
    """把诗句中 count 个字替换为 __，返回 (带空格的题目, 被遮住的字列表)。"""
    chars = list(line)
    # 排除首尾字，保留上下文
    indices = list(range(1, len(chars) - 1)) if len(chars) > 2 else list(range(len(chars)))
    if not indices:
        return line, []
    chosen = sorted(random.sample(indices, min(count, len(indices))))
    answer = [chars[i] for i in chosen]
    for i in chosen:
        chars[i] = "__"
    return "".join(chars), answer


# ─────────────────────────────────────────────────────
# 游戏状态
# ─────────────────────────────────────────────────────
@dataclass
class RoundState:
    full_line: str
    author: str
    title: str
    blanked: str
    answer: list[str]
    started_at: float
    finished: bool = False


# ─────────────────────────────────────────────────────
# 插件
# ─────────────────────────────────────────────────────
@register
class PoetryBlankPlugin(Plugin):
    key = "poetry_blank"
    display_name = "诗词填空"
    message_channels = {"incoming", "outgoing"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command = "poetry"
        self._reward = 10
        self._timeout = 120
        self._rounds: dict[int, RoundState] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._used_indices: dict[int, set[int]] = {}  # chat_id -> 已出过的题 index

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        self._command = cfg.get("command", "poetry")
        self._reward = cfg.get("reward", 10)
        self._timeout = cfg.get("timeout", 120)
        self.commands = {self._command: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[poetry_blank] 已启动，指令：{self._command}，奖励：{self._reward}")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        self._rounds.clear()
        self._locks.clear()
        self._used_indices.clear()
        if ctx.log:
            await ctx.log("info", "[poetry_blank] 已停止")

    def _pick_poem(self, chat_id: int) -> tuple[str, str, str, str, list[str]]:
        """随机选一首诗，挖空，返回 (原句, 作者, 题目, 带空题目, 答案)。"""
        used = self._used_indices.get(chat_id, set())
        available = [i for i in range(len(POEMS)) if i not in used]
        if not available:
            # 全部出过了，重置
            available = list(range(len(POEMS)))
            self._used_indices[chat_id] = set()
        idx = random.choice(available)
        self._used_indices.setdefault(chat_id, set()).add(idx)
        line, author, title = POEMS[idx]
        # 长句挖2个字，短句挖1个字
        blank_count = 2 if len(line) >= 5 else 1
        blanked, answer = _blank_line(line, blank_count)
        return line, author, title, blanked, answer

    async def _cmd_handler(
        self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext,
    ) -> None:
        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        lock = self._get_lock(chat_id)
        async with lock:
            # 已有进行中的题
            rd = self._rounds.get(chat_id)
            if rd and not rd.finished:
                await event.reply(
                    f"<b>📝 诗词填空</b>（进行中）\n\n"
                    f"<code>{rd.blanked}</code>\n"
                    f"作者：{rd.author} · 《{rd.title}》\n\n"
                    f"直接发答案就能抢答！",
                    parse_mode="html",
                )
                return

            # 出新题
            line, author, title, blanked, answer = self._pick_poem(chat_id)
            rd = RoundState(
                full_line=line,
                author=author,
                title=title,
                blanked=blanked,
                answer=answer,
                started_at=time.monotonic(),
            )
            self._rounds[chat_id] = rd

        await event.reply(
            f"<b>📝 诗词填空</b> · 奖励 {self._reward} 分\n\n"
            f"<code>{blanked}</code>\n\n"
            f"💡 提示：{author} · 《{title}》\n"
            f"直接发答案抢答！",
            parse_mode="html",
        )
        asyncio.create_task(self._auto_timeout(chat_id, ctx))

    async def on_message(self, ctx: PluginContext, event: Any) -> None:
        text = (getattr(event, "raw_text", "") or "").strip()
        if not text or text.startswith(",") or text.startswith("/"):
            return

        chat_id = int(getattr(event.chat_id, "channel_id", None) or event.chat_id or 0)
        if not chat_id:
            return

        rd = self._rounds.get(chat_id)
        if not rd or rd.finished:
            return

        # 检查答案
        if not self._check_answer(text, rd):
            return

        lock = self._get_lock(chat_id)
        async with lock:
            if rd.finished:
                return
            rd.finished = True

            sender = await event.get_sender()
            name = getattr(sender, "first_name", "") or "玩家"

            await event.reply(
                f"<b>🎉 答对了！</b>\n\n"
                f"✅ {rd.full_line}\n"
                f"📖 {rd.author} · 《{rd.title}》\n\n"
                f"🏆 {name} 获得 {self._reward} 分！\n"
                f"输入 ,{self._command} 继续下一题",
                parse_mode="html",
            )
            self._rounds.pop(chat_id, None)

    def _check_answer(self, text: str, rd: RoundState) -> bool:
        """校验答案：完整诗句 or 缺的那几个字。"""
        # 去掉标点和空格
        clean = text.replace("，", "").replace("。", "").replace("！", "").replace("？", "")
        clean = clean.replace(",", "").replace(".", "").replace(" ", "")

        # 完整诗句匹配
        full_clean = rd.full_line.replace("，", "").replace("。", "").replace("！", "").replace("？", "")
        if clean == full_clean:
            return True

        # 只答了缺的字
        answer_str = "".join(rd.answer)
        if clean == answer_str:
            return True

        return False

    async def _auto_timeout(self, chat_id: int, ctx: PluginContext) -> None:
        await asyncio.sleep(self._timeout)
        rd = self._rounds.get(chat_id)
        if rd and not rd.finished:
            rd.finished = True
            self._rounds.pop(chat_id, None)
            if ctx.log:
                await ctx.log("info", f"[poetry_blank] chat {chat_id} 填空超时，答案：{rd.full_line}")


PLUGIN_CLASS = PoetryBlankPlugin

__all__ = ["PoetryBlankPlugin", "PLUGIN_CLASS"]
