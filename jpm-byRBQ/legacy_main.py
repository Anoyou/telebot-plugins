"""
JPM 插件 - 关键词触发回复
触发方式: 在群组中发送 /关键词
"""

import asyncio
import contextlib
import json
import random
import time
from pathlib import Path
from typing import Optional, Dict, List

from pagermaid.listener import listener
from pagermaid.hook import Hook
from pagermaid.enums import Message, Client
from pagermaid.utils import logs


# 配置文件路径
plugin_dir = Path(__file__).parent
config_file = plugin_dir / "jpm_config.json"
trigger_log_file = plugin_dir / "jpm_trigger_log.json"

# 默认频率限制（秒）
DEFAULT_RATE_LIMIT = 3600


@Hook.on_startup()
async def plugin_startup():
    """插件初始化"""
    logs.info("JPM 插件已加载")


@Hook.on_shutdown()
async def plugin_shutdown():
    """插件关闭"""
    logs.info("JPM 插件已卸载")


# 模板数据（来自 sao_nkr 发癫文案）
TEMPLATES_DATA = {
    "templates": [
        # 单人模板（21条）
        {
            "id": 1,
            "mode": "single",
            "content": "大家能教教{name} 怎么骂人喵\n\n每次骂人{name} 都觉得不好意思\n\n捏紧了拳头👊\n\n憋红了脸😳\n\n最后只能小声地说一句\n\n你坏死了喵😻",
        },
        {
            "id": 2,
            "mode": "single",
            "content": "夜深风轻，烛影摇曳，{name} 静坐窗前，指尖轻抚茶杯边缘，心中翻涌着一丝丝悄悄的热意。眼神望向空无一人的暗角，却仿佛看见了心底的渴望在跳动，轻轻、温柔、又不可抵挡。每一次呼吸，都像与夜色缠绵无声，却让身体悄悄回应。",
        },
        {
            "id": 3,
            "mode": "single",
            "content": "风吹窗纱，月色洒肩，{name} 躺在榻上，任思绪如轻雾般缠绕全身。心里那股悄悄翻涌的欢愉，让呼吸都带上甜味。夜静而人心乱，仿佛连空气都懂得心跳，轻轻在耳边低语，暗示着未曾触碰的渴望。",
        },
        {
            "id": 4,
            "mode": "single",
            "content": "{name} 手握书卷，却读不进去一字一句。烛光摇曳，倒映在眼里，像心底涌动的暗流。呼吸微微沉重，每一次心跳都似被夜色拉长，像有看不见的手，轻轻挑动每一寸敏感的神经，让夜晚比白昼更热烈。",
        },
        {
            "id": 5,
            "mode": "single",
            "content": "月色柔和，风吹檐角，{name} 斜靠在窗前，肩头洒落斑驳光影。思绪偷偷翻滚，像悄悄触碰了身体的每一根神经。心里的渴望像暗潮般涌起，呼吸也随之轻重起伏，微不可闻，却让整个人像被夜色温柔包裹。",
        },
        {
            "id": 6,
            "mode": "single",
            "content": "屋内静寂，烛火半斜，{name} 一杯茶在手，却觉空气里满是未曾触碰的热意。指尖无意滑过桌面，心却轻轻荡漾。思绪像风一样穿过夜色，悄悄探入身体每一寸空隙，轻柔，却充满诱惑。",
        },
        {
            "id": 7,
            "mode": "single",
            "content": "深夜无人，{name} 躺在榻上，眼神望向天花板，心里却已在另一片光影中漂浮。身体虽静，心却波涛汹涌，每一次呼吸都像被无形的手轻抚，温热而不声张，暗暗唤动最深的渴望。",
        },
        {
            "id": 8,
            "mode": "single",
            "content": "风轻轻摇动窗帘，烛火映照脸庞，{name} 低眉沉思，心中微微颤动。像是空气里悄悄溶入了渴望，指尖轻轻触碰衣角，心却早已走向夜色里最温柔的角落，让夜晚的温度升高。",
        },
        {
            "id": 9,
            "mode": "single",
            "content": "{name} 静坐窗前，茶香袅袅，心里暗暗翻腾。每一次呼吸，都像与空气悄悄缠绕。月色透过玻璃洒在肩头，像有人轻轻触碰，又像整个夜晚都被心里的热意包裹。",
        },
        {
            "id": 10,
            "mode": "single",
            "content": "烛火跳动，影子轻摇，{name} 轻抚指尖，心里的暗涌却无法停息。夜深人静，呼吸逐渐沉重，身体与心思都在悄悄回应，像夜色偷偷替他拉近了距离，让渴望在心中悄悄燃起。",
        },
        {
            "id": 11,
            "mode": "single",
            "content": "夜色轻柔，烛光半斜，{name} 倚窗而坐，眼神透着难以言说的热意。思绪翻滚如潮，悄悄在身体里生根发芽。每一次呼吸都带着微微的颤动，让整个夜晚像被心里的渴望悄悄点燃。",
        },
        {
            "id": 12,
            "mode": "single",
            "content": "{name} 躺在榻上，月光透过窗纱洒在身上，像为他镀了一层温热的光。思绪在夜里偷偷游走，悄悄触碰每一寸敏感的神经，心跳微微加速，像在与夜色共舞。",
        },
        {
            "id": 13,
            "mode": "single",
            "content": "烛影摇曳，风吹窗帘，{name} 指尖轻抚茶杯边，心里却像有千丝万缕的悄悄缠绕。呼吸沉重又微妙，每一次心跳都像在暗中回应未曾触碰的温度，让夜晚温柔而热烈。",
        },
        {
            "id": 14,
            "mode": "single",
            "content": "风吹檐角，月色洒在窗台，{name} 斜靠而坐，心思暗暗生动。像有无形的手轻轻挑动每一根神经，呼吸轻重起伏，夜色也似乎被悄悄拉近，让身体与思绪交错温热。",
        },
        {
            "id": 15,
            "mode": "single",
            "content": "{name} 端坐榻上，烛光半斜，指尖无意滑过桌面。思绪偷偷探向心底最深的渴望，微微颤动，像有人轻抚，又像整个夜晚都在回应他隐秘的热意。",
        },
        {
            "id": 16,
            "mode": "single",
            "content": "夜深人静，风轻轻吹动窗纱，{name} 躺卧榻上，眼神远望。心中暗潮汹涌，每一次呼吸都像被夜色轻抚，微热而悄悄引动每一寸肌理，让渴望在无声中升腾。",
        },
        {
            "id": 17,
            "mode": "single",
            "content": "烛火半斜，香气微熏，{name} 静坐一隅，眼神迷离。思绪像风一样穿梭全身，每一次微微的呼吸都悄悄回应心底最隐秘的欲望，让夜晚温柔却热烈。",
        },
        {
            "id": 18,
            "mode": "single",
            "content": "风轻轻吹动窗帘，月色洒在榻上，{name} 心里暗暗翻腾。像空气里潜入了悄悄的渴望，每一次呼吸都带着微热的颤动，让夜晚像被秘密轻轻点燃。",
        },
        {
            "id": 19,
            "mode": "single",
            "content": "{name} 静卧窗前，茶香袅袅，心中暗涌如水。每一次呼吸都像在悄悄回应夜色，像有人轻轻触碰每一寸敏感的神经，让心跳随着思绪跳动。",
        },
        {
            "id": 20,
            "mode": "single",
            "content": "夜深人静，烛影摇曳，{name} 静坐窗前，指尖轻触桌面。思绪悄悄翻涌，心底的渴望像暗潮般生动，每一次呼吸都轻轻回应夜色的温柔，像秘密在悄悄蔓延。",
        },
        {
            "id": 21,
            "mode": "single",
            "content": "{name} 静卧榻上，风吹窗纱发出轻响，心却已像火焰般翻滚。思绪偷偷探向身体的每一寸神经，每一次微动都像有人轻抚，暗示着未曾触碰的温度，让夜晚温柔又热烈。",
        },
        # 双人模板（20条）
        {
            "id": 101,
            "mode": "dual",
            "content": "茶香未散，话还未起，{name} 偷眼看 {target}，只觉月色都柔软起来。旁人尚能端坐，唯 {name} 与 {target} 早已暗生情思，心跳轻轻对撞，却又装作若无其事。",
        },
        {
            "id": 102,
            "mode": "dual",
            "content": "烛光摇曳，帘影轻动，{name} 与 {target} 坐对，言语平淡如水，眼神却暗暗交锋。风吹入室，像替两人轻轻挑动心弦，让旁人看去，只道这是寻常寒暄。",
        },
        {
            "id": 103,
            "mode": "dual",
            "content": "风吹檐角，月色斜洒，{name} 的目光落在 {target} 肩头，暗暗笑了。{target} 偏偏眼角微挑，心里暗自明白：此番交谈，已不只关乎言语。",
        },
        {
            "id": 104,
            "mode": "dual",
            "content": "人前 {name} 端坐如君子，{target} 神色若无其事；\n人后风波已起，二人心思暗暗较劲，笑意轻轻流动，像夜色替他们低声作证。",
        },
        {
            "id": 105,
            "mode": "dual",
            "content": "一杯茶落地聲，烛影斜照，{name} 微微靠近 {target}，指尖轻触桌面，却仿佛撩动了空气里最柔软的热意。旁人尚未察觉，风月却暗暗作伴。",
        },
        {
            "id": 106,
            "mode": "dual",
            "content": "夜深无声，{name} 与 {target} 交谈间，眼神早已彼此交换暗号。每一次呼吸都像轻轻互试分寸，话虽平常，心已翻腾，旁观者自会偷笑。",
        },
        {
            "id": 107,
            "mode": "dual",
            "content": "风吹窗纱，烛影摇曳，{name} 偷看 {target} 时，心里暗暗痒。{target} 微微侧身，眼波一转，心中早已明白，这夜色里，两人各自藏着一段小秘密。",
        },
        {
            "id": 108,
            "mode": "dual",
            "content": "茶未凉，话未尽，{name} 与 {target} 眉目之间暗藏波澜。轻轻一笑，如风轻拂过心田，旁人尚在闲谈，却不知他们的心已悄悄缠绕。",
        },
        {
            "id": 109,
            "mode": "dual",
            "content": "烛火半斜，影子晃动，{name} 与 {target} 静坐，指尖轻轻摩挲桌面。旁人只觉两人沉默，却不知沉默下是暗潮汹涌，心思交错比言语更难防。",
        },
        {
            "id": 110,
            "mode": "dual",
            "content": "夜色温柔，{name} 偷眼看向 {target}，月光下两人影子交错。风吹帘动，仿佛替他们轻轻撩动心弦，心思已悄悄越界，却仍笑作若无其事。",
        },
        {
            "id": 111,
            "mode": "dual",
            "content": "人前 {name} 言辞稳重，{target} 神色淡然；\n人后二人心思暗生，一言一笑间，仿佛整座屋子都被无声的热意缠绕。",
        },
        {
            "id": 112,
            "mode": "dual",
            "content": "风未起，月色未深，{name} 与 {target} 的目光却暗暗交错。茶香绕指，烛影摇曳，二人心思像暗潮一般涌动，旁人看去，不过是一场寻常寒暄。",
        },
        {
            "id": 113,
            "mode": "dual",
            "content": "话未及口，心已飞远。{name} 偏眼看 {target} 一眼，心跳轻轻加速。旁人尚未察觉，风月早已在暗中作伴，将二人的秘密轻轻推开。",
        },
        {
            "id": 114,
            "mode": "dual",
            "content": "夜深人静，{name} 与 {target} 交谈，言辞平淡，眉眼却暗暗调情。风吹进屋，像替两人拉近距离，让夜色也偷偷参与他们的心事。",
        },
        {
            "id": 115,
            "mode": "dual",
            "content": "烛影斜照，{name} 手轻触茶杯边，目光落在 {target} 身上。微微的心动像悄悄传染了空气，旁人只觉平静，其实暗潮早已翻滚。",
        },
        {
            "id": 116,
            "mode": "dual",
            "content": "月色透窗，风吹檐角，{name} 与 {target} 对坐如画。微微的呼吸互相碰触，心思暗暗较劲，笑意却都藏在眼底，旁人看去不过是夜深闲坐。",
        },
        {
            "id": 117,
            "mode": "dual",
            "content": "茶香袅袅，烛火微摇，{name} 与 {target} 心思如暗潮轻轻起伏。轻声交谈间，彼此眼神中已有万千暗示，旁观者只道两人沉静如常。",
        },
        {
            "id": 118,
            "mode": "dual",
            "content": "夜深风轻，{name} 偏头望向 {target}，微微一笑。{target} 亦眉眼含笑，心里早已悄悄明白：此夜，二人各自心里暗暗燃起火焰。",
        },
        {
            "id": 119,
            "mode": "dual",
            "content": "人前端坐如常，{name} 与 {target} 心思却暗暗较劲。每一次目光相接，像是在互相试探，旁人只觉两人沉默，实则暗潮翻涌，比言语更撩人。",
        },
        {
            "id": 120,
            "mode": "dual",
            "content": "烛火跳动，风吹帘角，{name} 与 {target} 目光轻轻交错。心思暗生，呼吸微微加重，仿佛夜色都替他们低语，旁观者只能偷偷笑叹，明白又不得说破。",
        },
    ]
}


class TemplateGenerator:
    """模板生成器"""

    def __init__(self):
        self.single_templates: List[str] = []
        self.dual_templates: List[str] = []
        self.load_templates()

    def load_templates(self):
        """加载模板"""
        for template in TEMPLATES_DATA["templates"]:
            if template["mode"] == "single":
                self.single_templates.append(template["content"])
            else:
                self.dual_templates.append(template["content"])
        logs.info(
            f"已加载 {len(self.single_templates)} 个单人模板和 {len(self.dual_templates)} 个双人模板"
        )

    def generate_single(self, name: str) -> str:
        """生成单人回复"""
        if not self.single_templates:
            return f"{name} 收到了消息"
        template = random.choice(self.single_templates)
        return template.replace("{name}", name).replace("{target_user}", name)

    def generate_dual(self, keyword: str, target_user: str) -> str:
        """生成双人回复（{name} 是关键词，{target} 是目标用户）"""
        if not self.dual_templates:
            return f"{keyword} 和 {target_user} 的故事"
        template = random.choice(self.dual_templates)
        return template.replace("{name}", keyword).replace("{target}", target_user)


class JPMConfigManager:
    """配置管理类"""

    def __init__(self):
        self.enabled: bool = False  # 插件总开关，控制所有关键词是否生效
        self.owner_id: Optional[int] = None  # 插件所有者ID，只有所有者可以管理配置
        self.keywords: Dict[
            str, Dict
        ] = {}  # keyword -> {target_user_id, target_chat_id, rate_limit_seconds, anchor_message_id}
        self.load()

    def load(self) -> None:
        """从文件加载配置"""
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.enabled = data.get("enabled", False)
                    self.owner_id = data.get("owner_id")
                    self.keywords = data.get("keywords", {})
                logs.info(f"JPM 配置已加载，共 {len(self.keywords)} 个关键词")
            except Exception as e:
                logs.error(f"加载 JPM 配置失败: {e}")
                # 重置所有属性，避免数据不一致
                self.enabled = False
                self.owner_id = None
                self.keywords = {}
        else:
            self.keywords = {}

    def save(self) -> bool:
        """保存配置到文件"""
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "enabled": self.enabled,
                        "owner_id": self.owner_id,
                        "keywords": self.keywords,
                    },
                    f,
                    indent=4,
                    ensure_ascii=False,
                )
            logs.debug("JPM 配置已保存")
            return True
        except Exception as e:
            logs.error(f"保存 JPM 配置失败: {e}")
            return False

    def add_keyword(
        self,
        keyword: str,
        target_user_id: int,
        target_chat_id: int,
        rate_limit: int = DEFAULT_RATE_LIMIT,
    ) -> str:
        """添加或更新关键词配置（保留已有的锚点消息ID）"""
        # 参数验证
        if not keyword or not keyword.strip():
            return "关键词不能为空"
        if rate_limit < 0:
            return "频率限制必须大于等于0"

        # 保留已有的锚点消息ID
        existing_anchor = None
        if keyword in self.keywords:
            existing_anchor = self.keywords[keyword].get("anchor_message_id")

        self.keywords[keyword] = {
            "target_user_id": target_user_id,
            "target_chat_id": target_chat_id,
            "rate_limit_seconds": rate_limit,
            "anchor_message_id": existing_anchor,
        }
        self.save()
        return f"关键词 `{keyword}` 配置已更新"

    def set_anchor(self, keyword: str, anchor_message_id: int) -> str:
        """设置关键词的锚点消息ID"""
        if keyword not in self.keywords:
            return f"关键词 `{keyword}` 不存在"

        self.keywords[keyword]["anchor_message_id"] = anchor_message_id
        self.save()
        return f"关键词 `{keyword}` 的锚点消息已设置"

    def get_anchor(self, keyword: str) -> Optional[int]:
        """获取关键词的锚点消息ID"""
        config = self.keywords.get(keyword)
        return config.get("anchor_message_id") if config else None

    def clear_anchor(self, keyword: str) -> str:
        """清除关键词的锚点消息ID"""
        if keyword not in self.keywords:
            return f"关键词 `{keyword}` 不存在"

        if "anchor_message_id" in self.keywords[keyword]:
            del self.keywords[keyword]["anchor_message_id"]
            self.save()
            return f"关键词 `{keyword}` 的锚点消息已清除"
        return f"关键词 `{keyword}` 没有设置锚点消息"

    def delete_keyword(self, keyword: str) -> tuple[bool, str]:
        """删除关键词配置"""
        if keyword in self.keywords:
            del self.keywords[keyword]
            self.save()
            return True, f"关键词 `{keyword}` 已删除"
        return False, f"关键词 `{keyword}` 不存在"

    def get_keyword_config(self, keyword: str) -> Optional[Dict]:
        """获取关键词配置"""
        return self.keywords.get(keyword)

    def list_keywords(self) -> str:
        """列出所有关键词配置"""
        if not self.keywords:
            return "暂无关键词配置"
        lines = ["**关键词配置列表：**"]
        for keyword, config in self.keywords.items():
            lines.append(
                f"- `{keyword}` → 用户: `{config['target_user_id']}`, 群组: `{config['target_chat_id']}`, 限制: {config['rate_limit_seconds']}秒"
            )
        return "\n".join(lines)


class TriggerLogManager:
    """触发记录管理类"""

    def __init__(self):
        self.logs: Dict[str, float] = {}  # keyword -> last_trigger_time
        self.load()

    def load(self) -> None:
        """从文件加载触发记录"""
        if trigger_log_file.exists():
            try:
                with open(trigger_log_file, "r", encoding="utf-8") as f:
                    self.logs = json.load(f)
                logs.info(f"触发记录已加载，共 {len(self.logs)} 条")
            except Exception as e:
                logs.error(f"加载触发记录失败: {e}")
                self.logs = {}
        else:
            self.logs = {}

    def save(self) -> None:
        """保存触发记录到文件"""
        try:
            with open(trigger_log_file, "w", encoding="utf-8") as f:
                json.dump(self.logs, f, indent=4)
        except Exception as e:
            logs.error(f"保存触发记录失败: {e}")

    def can_trigger(self, keyword: str, is_owner: bool) -> tuple[bool, Optional[int]]:
        """
        检查关键词是否可以触发
        返回: (是否可以触发, 需要等待的秒数)
        """
        # 主人无限制
        if is_owner:
            return True, None

        # 检查频率限制
        if keyword in self.logs:
            last_time = self.logs[keyword]
            elapsed = time.time() - last_time
            keyword_config = config_manager.get_keyword_config(keyword)
            if keyword_config:
                rate_limit = keyword_config.get(
                    "rate_limit_seconds", DEFAULT_RATE_LIMIT
                )
                if elapsed < rate_limit:
                    wait_time = int(rate_limit - elapsed)
                    return False, wait_time

        return True, None

    def record_trigger(self, keyword: str) -> None:
        """记录关键词触发时间"""
        self.logs[keyword] = time.time()
        self.save()

    def clear_keyword(self, keyword: str) -> None:
        """清除关键词的触发记录"""
        if keyword in self.logs:
            del self.logs[keyword]
            self.save()


# 全局实例
config_manager = JPMConfigManager()
trigger_log = TriggerLogManager()
template_generator = TemplateGenerator()


@listener(
    command="jpm",
    description="JPM 插件管理",
    parameters="<on|off|set|delete|list|owner|status|anchor>",
    is_plugin=True,
)
async def jpm_command(message: Message):
    """处理 jpm 管理命令"""
    # 获取命令参数
    text = message.arguments or ""

    # 如果没有参数，返回提示信息并在3秒后撤回
    if not text or text.strip() == "":
        await message.edit("请输入文本")
        await asyncio.sleep(3)
        await message.delete()
        return

    # 检查是否是帮助命令
    if text.strip().lower() == "help":
        await show_help(message)
        return

    cmd = text.lower().split()[0]

    if cmd == "on":
        await enable_feature(message)
    elif cmd == "off":
        await disable_feature(message)
    elif cmd == "set":
        await set_keyword(message)
    elif cmd == "delete":
        await delete_keyword(message)
    elif cmd == "list":
        await list_keywords(message)
    elif cmd == "owner":
        await set_owner(message)
    elif cmd == "status":
        await show_status(message)
    elif cmd == "anchor":
        await manage_anchor(message)
    else:
        await show_help(message)


def check_permission(message: Message) -> bool:
    """
    检查消息发送者是否有权限执行管理命令
    主人可以执行所有命令
    如果未设置主人ID，则允许任何人执行（用于首次配置）
    """
    if config_manager.owner_id is None:
        # 未设置主人，允许任何人操作（用于首次配置）
        return True

    return message.from_user.id == config_manager.owner_id


async def show_help(message: Message):
    """显示帮助信息"""
    help_text = """**JPM 插件使用说明:**

**,jpm on** - 开启全局功能
**,jpm off** - 关闭全局功能
**,jpm set <关键词> <用户ID> <群组ID> [秒数]** - 添加/更新关键词配置
**,jpm delete <关键词>** - 删除关键词配置
**,jpm list** - 列出所有关键词配置
**,jpm owner <用户ID>** - 设置主人ID
**,jpm status** - 查看当前状态
**,jpm anchor <set|clear> <关键词> [消息ID]** - 管理锚点消息

**触发方式:**
- 在群组中发送 `/关键词` 触发对应配置的回复

**锚点说明:**
- 插件会自动记录目标用户的最新发言作为锚点
- 即使目标用户长时间未发言，也能通过锚点消息进行回复
- 手动设置：回复一条消息后使用 `,jpm anchor set <关键词>`
- 清除锚点：`,jpm anchor clear <关键词>`

**频率限制:**
- 主人触发：无限制
- 其他人触发：每个关键词独立计算频率限制"""
    await message.edit(help_text)


async def enable_feature(message: Message):
    """开启全局功能"""
    if not check_permission(message):
        await message.edit("❌ 权限不足！只有主人可以执行此操作")
        return

    config_manager.enabled = True
    config_manager.save()

    if not config_manager.keywords:
        await message.edit(
            "⚠️ JPM 功能已开启，但尚未配置关键词\n使用 `,jpm set <关键词> <用户ID> <群组ID>` 添加配置"
        )
    else:
        await message.edit(
            f"✅ JPM 功能已开启\n已配置 {len(config_manager.keywords)} 个关键词"
        )


async def disable_feature(message: Message):
    """关闭全局功能"""
    if not check_permission(message):
        await message.edit("❌ 权限不足！只有主人可以执行此操作")
        return

    config_manager.enabled = False
    config_manager.save()
    await message.edit("❌ JPM 功能已关闭")


async def set_keyword(message: Message):
    """设置关键词配置"""
    if not check_permission(message):
        await message.edit("❌ 权限不足！只有主人可以执行此操作")
        return

    params = message.arguments.split()
    if len(params) < 4:
        await message.edit(
            "❌ 参数错误！\n使用 `,jpm set <关键词> <用户ID> <群组ID> [秒数]`"
        )
        return

    try:
        keyword = params[1]
        user_id = int(params[2])
        chat_id = int(params[3])
        rate_limit = int(params[4]) if len(params) > 4 else DEFAULT_RATE_LIMIT

        msg = config_manager.add_keyword(keyword, user_id, chat_id, rate_limit)
        await message.edit(
            f"✅ {msg}\n用户ID: `{user_id}`\n群组ID: `{chat_id}`\n频率限制: {rate_limit}秒"
        )
    except ValueError:
        await message.edit("❌ ID格式错误！请输入有效的数字ID")


async def delete_keyword(message: Message):
    """删除关键词配置"""
    if not check_permission(message):
        await message.edit("❌ 权限不足！只有主人可以执行此操作")
        return

    params = message.arguments.split()
    if len(params) < 2:
        await message.edit("❌ 参数错误！\n使用 `,jpm delete <关键词>`")
        return

    keyword = params[1]
    success, msg = config_manager.delete_keyword(keyword)
    if success:
        trigger_log.clear_keyword(keyword)
    await message.edit(f"{'✅' if success else '❌'} {msg}")


async def list_keywords(message: Message):
    """列出所有关键词配置"""
    result = config_manager.list_keywords()
    await message.edit(result)


async def set_owner(message: Message):
    """设置主人ID"""
    # 特殊处理：如果未设置主人ID，则允许任何人设置
    if config_manager.owner_id is not None and not check_permission(message):
        await message.edit("❌ 权限不足！只有主人可以执行此操作")
        return

    params = message.arguments.split()
    if len(params) < 2:
        await message.edit("❌ 参数错误！\n使用 `,jpm owner <用户ID>`")
        return

    try:
        owner_id = int(params[1])
        config_manager.owner_id = owner_id
        config_manager.save()
        await message.edit(f"✅ 主人ID已设置为: `{owner_id}`")
    except ValueError:
        await message.edit("❌ ID格式错误！请输入有效的数字ID")


async def show_status(message: Message):
    """显示当前状态"""
    status = "✅ 已开启" if config_manager.enabled else "❌ 已关闭"
    owner_info = f"`{config_manager.owner_id}`" if config_manager.owner_id else "未设置"
    keywords_list = config_manager.list_keywords()

    status_text = f"""**JPM 插件状态:**

功能状态: {status}
主人ID: {owner_info}

{keywords_list}

**频率限制:** 主人无限制，其他人按关键词独立计算

**触发方式:** `/关键词`"""
    await message.edit(status_text)


async def manage_anchor(message: Message):
    """管理锚点消息"""
    if not check_permission(message):
        await message.edit("❌ 权限不足！只有主人可以执行此操作")
        return

    params = message.arguments.split()
    if len(params) < 2:
        await message.edit(
            "❌ 参数错误！\n使用 `,jpm anchor <set|clear> <关键词> [消息ID]`"
        )
        return

    action = params[1].lower()
    keyword = params[2] if len(params) > 2 else None

    if not keyword:
        await message.edit("❌ 请指定关键词")
        return

    if action == "set":
        # 设置锚点：优先使用回复的消息ID，其次是参数中的ID
        message_id = None

        # 如果回复了消息，使用被回复消息的ID
        if message.reply_to_message:
            message_id = message.reply_to_message.id
        # 否则使用参数中的消息ID
        elif len(params) > 3:
            try:
                message_id = int(params[3])
            except ValueError:
                await message.edit("❌ 消息ID格式错误！请输入有效的数字ID")
                return
        else:
            await message.edit(
                "❌ 请回复一条消息或指定消息ID\n使用方法：回复一条消息后发送 `,jpm anchor set <关键词>`"
            )
            return

        result = config_manager.set_anchor(keyword, message_id)
        await message.edit(f"✅ {result}\n锚点消息ID: `{message_id}`")

    elif action == "clear":
        # 清除锚点
        result = config_manager.clear_anchor(keyword)
        await message.edit(f"{'✅' if '已清除' in result else '❌'} {result}")
    else:
        await message.edit("❌ 未知操作！使用 `set` 或 `clear`")


async def get_target_user_last_message(
    client: Client, chat_id: int, user_id: int, limit: int = 100
):
    """获取指定用户在群组中的最近一条消息"""
    try:
        async for msg in client.get_chat_history(chat_id, limit=limit):
            if msg.from_user and msg.from_user.id == user_id:
                return msg
        return None
    except Exception as e:
        logs.error(f"获取用户消息失败: {e}")
        return None


@listener(is_plugin=True, incoming=True, outgoing=False, ignore_edited=True)
async def track_anchor_messages(message: Message, bot: Client):
    """自动记录目标用户的发言作为锚点消息"""
    # 只处理群组消息
    if not message.chat or message.chat.id >= 0:
        return

    # 获取发送者ID
    if not message.from_user:
        return

    sender_id = message.from_user.id
    chat_id = message.chat.id
    message_id = message.id

    # 检查是否有任何关键词配置了这个用户作为目标
    for keyword, config in config_manager.keywords.items():
        if (
            config["target_user_id"] == sender_id
            and config["target_chat_id"] == chat_id
        ):
            # 更新锚点消息ID
            config["anchor_message_id"] = message_id
            config_manager.save()
            logs.debug(f"[JPM] 更新关键词 `{keyword}` 的锚点消息: {message_id}")
            break  # 一个用户只处理一次


@listener(is_plugin=True, incoming=True, outgoing=True, ignore_edited=True)
async def trigger_jpm(message: Message, bot: Client):
    """触发 jpm 回复 - 只处理 /关键词 格式的消息"""
    text = message.text or ""

    # 快速过滤：只处理以 / 开头的消息
    if not text.startswith("/"):
        return

    # 提取关键词和参数
    parts = text[1:].strip().split()
    keyword = parts[0] if parts else ""
    param = parts[1] if len(parts) > 1 else None  # 获取参数（第二个词）

    if not keyword:
        return

    # 快速过滤：只处理已配置的关键词
    keyword_config = config_manager.get_keyword_config(keyword)
    if not keyword_config:
        return

    # 快速过滤：只在目标群组中处理
    if message.chat.id != keyword_config["target_chat_id"]:
        return

    # 检查功能是否开启
    if not config_manager.enabled:
        logs.info(f"[JPM] 关键词 `/{keyword}` 被触发，但功能未开启")
        return

    # 获取触发用户ID
    trigger_user_id = message.from_user.id if message.from_user else None
    if not trigger_user_id:
        return

    # 检查是否是主人
    is_owner = (
        (trigger_user_id == config_manager.owner_id)
        if config_manager.owner_id
        else False
    )

    # 检查频率限制
    can_trigger, wait_time = trigger_log.can_trigger(keyword, is_owner)
    if not can_trigger:
        logs.info(
            f"[JPM] 用户 {trigger_user_id} 触发 `/{keyword}` 过于频繁，需等待 {wait_time} 秒"
        )
        return

    # 判断使用单人还是双人模板
    # 单人：直接发送 /关键词（不回复任何人且不带参数）
    # 双人：回复某人 或 带参数（/关键词 xxx）
    is_reply_to_someone = message.reply_to_message is not None
    has_param = param is not None

    use_dual = is_reply_to_someone or has_param

    # 生成回复内容
    with contextlib.suppress(Exception):
        target_message = None
        anchor_message_id = keyword_config.get("anchor_message_id")

        # 优先使用锚点消息
        if anchor_message_id:
            try:
                target_message = await bot.get_messages(
                    message.chat.id, anchor_message_id
                )
                logs.debug(f"[JPM] 使用锚点消息: {anchor_message_id}")
            except Exception as e:
                logs.warning(
                    f"[JPM] 获取锚点消息 {anchor_message_id} 失败: {e}，尝试查找最近发言"
                )

        # 如果没有锚点消息，则查找最近发言
        if not target_message:
            target_message = await get_target_user_last_message(
                bot, message.chat.id, keyword_config["target_user_id"]
            )

        if target_message and target_message.from_user:
            target_name = (
                target_message.from_user.username
                or target_message.from_user.first_name
                or str(target_message.from_user.id)
            )

            if use_dual:
                # 双人模式：确定第二个名字
                if has_param:
                    # 优先使用参数
                    second_name = param
                    mode_desc = f"双人(关键词+参数:{param})"
                elif is_reply_to_someone and message.reply_to_message.from_user:
                    # 使用被回复者的名字
                    replied_user = message.reply_to_message.from_user
                    second_name = (
                        replied_user.username
                        or replied_user.first_name
                        or str(replied_user.id)
                    )
                    mode_desc = f"双人(关键词+回复:{second_name})"
                else:
                    # 降级到单人
                    second_name = None
                    mode_desc = "单人"

                if second_name:
                    reply_text = template_generator.generate_dual(keyword, second_name)
                    logs.info(
                        f"[JPM] `/{keyword}` 触发双人模式: {keyword} + {second_name}"
                    )
                else:
                    reply_text = template_generator.generate_single(keyword)
                    logs.info(f"[JPM] `/{keyword}` 触发单人模式: {keyword}")
            else:
                # 单人模式
                reply_text = template_generator.generate_single(keyword)
                logs.info(f"[JPM] `/{keyword}` 触发单人模式: {keyword}")

            await target_message.reply(reply_text)

            # 记录触发时间
            trigger_log.record_trigger(keyword)

            # 删除触发的命令消息
            with contextlib.suppress(Exception):
                await message.delete()
        else:
            logs.warning(
                f"[JPM] 未找到目标用户 {keyword_config['target_user_id']} 的回复目标"
            )
