"""
AI 查询插件 - 向AI模型提问并返回回复

支持：
- OpenAI 格式的 API 调用
- MCP（Model Context Protocol）工具集成（可选）
"""

import asyncio
import html
import json
import re
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import aiohttp

from pagermaid.listener import listener
from pagermaid.enums import Client, Message
from pagermaid.utils import logs
from pyrogram.types import InputMediaPhoto

# 尝试导入 MCP 客户端（可选依赖）
try:
    from mcp_client import MCPClient, ConfigManager
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    MCPClient = None
    ConfigManager = None

# 数据目录和配置文件路径
DATA_DIR = Path("ai_query")
DATA_FILE = DATA_DIR / "config.json"
PENDING_SELECTION = {}  # 待选择的模型列表消息
DEFAULT_SEARCH_CONFIG = {
    "enabled": True,
    "max_results": 5,
}
DEFAULT_UI_CONFIG = {
    "expand_ai_response": True,  # AI 回复使用可折叠消息格式
}
DEFAULT_REQUEST_TIMEOUT = 120
SEARCH_PLANNER_TIMEOUT = 45
SEARCH_TIMEOUT = 20
IMAGE_FETCH_TIMEOUT = 15
IMAGE_RESULT_LIMIT = 2
IMAGE_RESULT_SCAN_LIMIT = 4
SEARCH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
CREATIVE_PREFIXES = (
    "写",
    "帮我写",
    "润色",
    "改写",
    "翻译",
    "总结",
    "扩写",
    "续写",
    "生成",
    "编一个",
    "虚构",
)
SEARCH_HINT_WORDS = (
    "谁",
    "什么",
    "哪家",
    "哪个",
    "哪位",
    "哪一个",
    "哪里",
    "何时",
    "时间",
    "地点",
    "为什么",
    "新闻",
    "最近",
    "最新",
    "报道",
    "事件",
    "背景",
    "资料",
    "信息",
    "介绍",
    "来源",
    "事实",
    "真实",
    "核实",
    "查询",
)
SEARCH_NOISE_WORDS = (
    "请问",
    "我想知道",
    "帮我查",
    "帮我搜",
    "帮我搜索",
    "给我查",
    "给我搜",
    "告诉我",
    "麻烦你",
    "能不能",
    "一下",
    "一下子",
    "这个",
    "这件事",
    "事情",
    "到底",
    "具体",
    "相关",
    "情况",
)
IMAGE_HINT_WORDS = (
    "图片",
    "照片",
    "长什么样",
    "长啥样",
    "长相",
    "样子",
    "logo",
    "头像",
    "截图",
    "界面",
    "海报",
)
ANSWER_SYSTEM_PROMPT = """你是一个偏事实核查风格的中文问答助手。

回答时必须遵守：
1. 先识别用户真正想知道的核心点，再围绕这个点直接作答，不要把问题偷换成泛泛的背景分析。
2. 如果用户在问“是谁 / 哪家公司 / 代表作 / 合作方 / 拖欠对象 / 时间 / 地点”，优先直接给出这些结论。
3. 回答基于已提供的信息，不要编造；无法确认就明确写“暂未确认”。
4. 不展示思考过程，不要长篇铺垫，不要空泛说教。
5. 尽量先给结论，再补充 2-4 条关键信息。"""

SEARCH_ANSWER_PROMPT = """你正在根据联网搜索结果回答用户问题。

请严格遵守：
1. 先判断用户真正想知道的目标信息是什么。
2. 优先直接回答目标信息，不要把回答重心偏到原因分析、价值判断或泛泛背景。
3. 仅依据给定搜索结果回答；若结果不足以确认，请明确说明“根据当前搜索结果暂未确认”。
4. 如果结果里出现多个候选对象，请列出最可能的对象，并说明区分依据。
5. 结尾附上“参考线索：”一行，列出 1-3 个来源名称或标题，不要粘贴长链接。"""
SEARCH_ROUTER_PROMPT = """你是一个联网搜索路由助手。

你的任务是判断：当前用户问题是否应该先联网搜索，再由模型回答。

请严格输出 JSON，不要输出 JSON 以外的任何文字，格式如下：
{
  "use_search": true,
  "intent": "用户真正想知道的核心信息，20字以内",
  "search_queries": ["搜索词1", "搜索词2"],
  "need_images": false,
  "image_queries": ["图片搜索词1"],
  "reason": "简短说明为什么要搜或为什么不用搜"
}

决策规则：
1. 涉及新闻、人物、公司、事件、作品、时间、地点、事实核查、实体识别、关系确认时，优先 use_search=true。
2. 用户虽然没写“谁/哪家/什么”，但如果本质上是在问一个现实世界事实，也应 use_search=true。
3. 纯创作、润色、改写、翻译、闲聊、主观建议，通常 use_search=false。
4. search_queries 需要贴近用户真实意图，避免空泛；最多 4 个。
5. 当问题聚焦具体人物、项目、产品、品牌、作品，且图片有助于识别对象时，need_images=true。
6. image_queries 只在 need_images=true 时填写，最多 2 个。
7. 如果 use_search=false，search_queries 返回空数组，need_images=false，image_queries 返回空数组。"""

# MCP 相关
if HAS_MCP:
    mcp_client: Optional[MCPClient] = None
    mcp_config_manager: Optional[ConfigManager] = None


def load_config() -> dict:
    """加载AI配置"""
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return normalize_config(data)
        except Exception as e:
            logs.error(f"加载配置失败: {e}")
            return normalize_config({})
    return normalize_config({})


def save_config(config: dict) -> bool:
    """保存AI配置"""
    try:
        config = normalize_config(config)
        DATA_DIR.mkdir(exist_ok=True, parents=True)
        DATA_FILE.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except Exception as e:
        logs.error(f"保存配置失败: {e}")
        return False


def get_current_model(config: dict) -> str:
    """获取当前使用的模型"""
    return config.get("current_model", "") or config.get("model", "")


def normalize_config(config: Optional[dict]) -> dict:
    """补齐默认配置并清洗异常值"""
    config = config.copy() if isinstance(config, dict) else {}

    raw_search = config.get("search")
    if not isinstance(raw_search, dict):
        raw_search = {}

    max_results = raw_search.get("max_results", DEFAULT_SEARCH_CONFIG["max_results"])
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = DEFAULT_SEARCH_CONFIG["max_results"]

    config["search"] = {
        "enabled": bool(raw_search.get("enabled", DEFAULT_SEARCH_CONFIG["enabled"])),
        "max_results": max(1, min(max_results, 8)),
    }

    # UI 配置
    raw_ui = config.get("ui")
    if not isinstance(raw_ui, dict):
        raw_ui = {}
    config["ui"] = {
        "expand_ai_response": bool(raw_ui.get("expand_ai_response", DEFAULT_UI_CONFIG["expand_ai_response"])),
    }
    return config


def get_search_config(config: dict) -> dict:
    """获取搜索配置"""
    return normalize_config(config)["search"]


def get_ui_config(config: dict) -> dict:
    """获取 UI 配置"""
    return normalize_config(config)["ui"]


def wrap_expandable(content: str) -> str:
    """将内容包装为 Telegram 可折叠 blockquote"""
    return f"<blockquote expandable>{content}</blockquote>"


def normalize_api_url(api_url: str) -> str:
    """将基础 URL 规范化为 chat completions 接口地址"""
    raw_url = (api_url or "").strip()
    if not raw_url:
        return ""

    normalized = raw_url.rstrip("/")
    lowered = normalized.lower()
    if lowered.endswith("/chat/completions"):
        return normalized

    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")

    if not path:
        return f"{normalized}/v1/chat/completions"

    if path.lower().endswith("/v1"):
        return f"{normalized}/chat/completions"

    return f"{normalized}/chat/completions"


def mask_api_key(api_key: str) -> str:
    """隐藏 API Key 的大部分内容"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return f"{api_key[:4]}..."
    return f"{api_key[:8]}..."


def format_dialog_message(question: str, answer: str, expand: bool = True) -> str:
    """格式化最终对话消息"""
    rendered_answer = wrap_expandable(answer) if expand else answer
    return (
        f"Ciao？：\n"
        f"{question}\n\n"
        f"Ciallo～(∠・ω< )⌒★：\n"
        f"{rendered_answer}"
    )


def is_retryable_ai_failure(result: Optional[str]) -> bool:
    """判断是否属于可重试/可切换模型的失败"""
    if not result:
        return True

    failure_prefixes = (
        "API调用失败",
        "调用异常",
        "请求超时",
        "响应解析失败",
        "空消息重试失败",
    )
    return any(result.startswith(prefix) for prefix in failure_prefixes)


def extract_text_from_content(content) -> str:
    """从不同格式的 content 中提取文本"""
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, str) and item.strip():
                chunks.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue

            item_type = str(item.get("type", "")).lower()
            text_value = item.get("text")

            if isinstance(text_value, dict):
                text_value = text_value.get("value") or text_value.get("text")

            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value.strip())
            elif item_type == "output_text":
                fallback_text = item.get("value") or item.get("content")
                if isinstance(fallback_text, str) and fallback_text.strip():
                    chunks.append(fallback_text.strip())

        return "\n".join(chunks).strip()

    if isinstance(content, dict):
        for key in ("text", "value", "content", "transcript", "refusal"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def extract_ai_response_text(result: dict) -> str:
    """从 OpenAI 兼容响应中提取回复文本"""
    if not isinstance(result, dict):
        return ""

    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}

        message = first_choice.get("message")
        if isinstance(message, dict):
            content = extract_text_from_content(message.get("content"))
            if content:
                return content

            for key in (
                "reasoning_content",
                "reasoning",
                "text",
                "refusal",
                "output_text",
            ):
                value = message.get(key)
                extracted = extract_text_from_content(value)
                if extracted:
                    return extracted

            audio = message.get("audio")
            if isinstance(audio, dict):
                extracted = extract_text_from_content(audio)
                if extracted:
                    return extracted

        delta = first_choice.get("delta")
        if isinstance(delta, dict):
            for key in (
                "content",
                "reasoning_content",
                "reasoning",
                "text",
                "refusal",
                "output_text",
            ):
                extracted = extract_text_from_content(delta.get(key))
                if extracted:
                    return extracted

        for key in (
            "text",
            "content",
            "reasoning_content",
            "reasoning",
            "refusal",
            "output_text",
        ):
            extracted = extract_text_from_content(first_choice.get(key))
            if extracted:
                return extracted

    for key in ("message", "content", "output_text", "text", "refusal"):
        extracted = extract_text_from_content(result.get(key))
        if extracted:
            return extracted

    return ""


def build_response_debug_preview(result: dict) -> str:
    """构造便于排查的响应预览"""
    if not isinstance(result, dict):
        return str(result)[:500]

    preview_payload = {}
    for key in ("id", "object", "model", "created"):
        if key in result:
            preview_payload[key] = result.get(key)

    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else choices[0]
        preview_payload["choice_0"] = first_choice

    if "usage" in result:
        preview_payload["usage"] = result.get("usage")

    return clean_search_text(json.dumps(preview_payload, ensure_ascii=False))


def should_retry_empty_response(result: dict) -> bool:
    """判断是否需要针对空消息做二次重试"""
    if not isinstance(result, dict):
        return False

    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        return False

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return False

    message = first_choice.get("message")
    if not isinstance(message, dict):
        return False

    return (
        message.get("content") is None
        and message.get("reasoning_content") is None
        and message.get("tool_calls") is None
    )


def should_use_web_search(question: str, search_config: dict) -> bool:
    """搜索决策失败时的兜底规则"""
    if not search_config.get("enabled", True):
        return False

    text = question.strip().lower()
    if not text:
        return False

    if text.startswith(CREATIVE_PREFIXES):
        return False

    if any(word in question for word in SEARCH_HINT_WORDS):
        return True

    if len(question) >= 8 and not re.search(r"[。！？]$", question):
        return True

    return False


def build_search_queries(question: str) -> list[str]:
    """为问题生成一组搜索词"""
    normalized = re.sub(r"\s+", " ", question).strip()
    cleaned = normalized
    for noise in SEARCH_NOISE_WORDS:
        cleaned = cleaned.replace(noise, "")

    cleaned = re.sub(r"[，。！？、“”\"'：:（）()【】\[\]]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    candidates = [normalized]
    if cleaned and cleaned != normalized:
        candidates.append(cleaned)

    if cleaned:
        if not any(keyword in cleaned for keyword in ("新闻", "事件", "公司")):
            candidates.append(f"{cleaned} 事件")
        if any(keyword in normalized for keyword in ("哪家公司", "哪个公司", "公司", "拖欠", "外包")):
            candidates.append(f"{cleaned} 公司")

    unique_queries = []
    for item in candidates:
        if item and item not in unique_queries:
            unique_queries.append(item)
    return unique_queries[:4]


def extract_json_object(raw_text: str) -> Optional[dict]:
    """从模型回复中提取 JSON 对象"""
    if not raw_text:
        return None

    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def normalize_search_plan(
    question: str,
    search_config: dict,
    raw_plan: Optional[dict] = None,
) -> dict:
    """规范化搜索计划"""
    fallback_use_search = should_use_web_search(question, search_config)
    fallback_queries = build_search_queries(question) if fallback_use_search else []
    fallback_need_images = fallback_use_search and should_fetch_preview_images(question)
    plan = raw_plan if isinstance(raw_plan, dict) else {}

    use_search = bool(plan.get("use_search", fallback_use_search))
    if not search_config.get("enabled", True):
        use_search = False

    if question.strip().lower().startswith(CREATIVE_PREFIXES):
        use_search = False

    raw_queries = plan.get("search_queries", [])
    if isinstance(raw_queries, str):
        raw_queries = [raw_queries]
    if not isinstance(raw_queries, list):
        raw_queries = []

    queries = []
    for item in raw_queries:
        if not isinstance(item, str):
            continue
        query = re.sub(r"\s+", " ", item).strip()
        if query and query not in queries:
            queries.append(query)

    if use_search and not queries:
        queries = fallback_queries

    need_images = bool(plan.get("need_images", fallback_need_images)) and use_search
    raw_image_queries = plan.get("image_queries", [])
    if isinstance(raw_image_queries, str):
        raw_image_queries = [raw_image_queries]
    if not isinstance(raw_image_queries, list):
        raw_image_queries = []

    image_queries = []
    for item in raw_image_queries:
        if not isinstance(item, str):
            continue
        query = re.sub(r"\s+", " ", item).strip()
        if query and query not in image_queries:
            image_queries.append(query)

    if need_images and not image_queries:
        image_queries = queries[:2] or fallback_queries[:2]

    return {
        "use_search": use_search,
        "intent": str(plan.get("intent", "")).strip()[:80],
        "reason": str(plan.get("reason", "")).strip()[:120],
        "search_queries": queries[:4],
        "need_images": need_images,
        "image_queries": image_queries[:2],
    }


def build_search_router_messages(question: str) -> list[dict]:
    """构造搜索决策消息"""
    return [
        {"role": "system", "content": SEARCH_ROUTER_PROMPT},
        {
            "role": "user",
            "content": (
                f"用户问题：{question}\n\n"
                "请只返回 JSON，不要附加解释。"
            ),
        },
    ]


def clean_search_text(raw_text: str) -> str:
    """清理搜索结果中的 HTML 文本"""
    text = re.sub(r"<.*?>", " ", raw_text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def looks_like_named_entity_query(question: str) -> bool:
    """粗略判断是否像人物/项目/产品名查询"""
    text = re.sub(r"\s+", " ", (question or "")).strip()
    if not text or len(text) > 24:
        return False
    if re.search(r"[，。！？!?]", text):
        return False
    return not any(word in text for word in SEARCH_HINT_WORDS)


def should_fetch_preview_images(question: str) -> bool:
    """在路由失败时，决定是否兜底抓取预览图"""
    text = (question or "").strip()
    if not text:
        return False
    if any(word in text for word in IMAGE_HINT_WORDS):
        return True
    return looks_like_named_entity_query(text)


def unwrap_search_url(url: str) -> str:
    """解析搜索引擎跳转链接"""
    if not url:
        return ""

    url = html.unescape(url)
    if url.startswith("//"):
        url = f"https:{url}"

    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target_url = parse_qs(parsed.query).get("uddg", [])
        if target_url:
            return unquote(target_url[0])
    return url


def parse_duckduckgo_results(page_text: str, max_results: int) -> list[dict]:
    """解析 DuckDuckGo HTML 搜索结果"""
    result_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.S,
    )
    results = []
    seen_urls = set()

    for match in result_pattern.finditer(page_text):
        url = unwrap_search_url(match.group("url"))
        title = clean_search_text(match.group("title"))
        if not url or not title or url in seen_urls:
            continue

        nearby_text = page_text[match.end(): match.end() + 2000]
        snippet_match = re.search(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>',
            nearby_text,
            re.S,
        )
        snippet = clean_search_text(snippet_match.group(1)) if snippet_match else ""

        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
            }
        )
        seen_urls.add(url)

        if len(results) >= max_results:
            break

    return results


async def duckduckgo_search(query: str, max_results: int) -> list[dict]:
    """使用 DuckDuckGo HTML 进行搜索"""
    headers = {
        "User-Agent": SEARCH_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://duckduckgo.com/",
    }
    params = {
        "q": query,
        "kl": "cn-zh",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=SEARCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://html.duckduckgo.com/html/",
                headers=headers,
                params=params,
            ) as response:
                if response.status != 200:
                    logs.warning(f"DuckDuckGo 搜索失败: {response.status}")
                    return []
                page_text = await response.text()
                return parse_duckduckgo_results(page_text, max_results)
    except asyncio.TimeoutError:
        logs.warning("DuckDuckGo 搜索超时")
        return []
    except Exception as e:
        logs.warning(f"DuckDuckGo 搜索异常: {e}")
        return []


async def search_web(question: str, max_results: int) -> tuple[list[dict], str]:
    """执行联网搜索并返回结果"""
    queries = build_search_queries(question)
    merged_results = []
    seen_urls = set()
    used_query = ""

    for query in queries:
        results = await duckduckgo_search(query, max_results)
        if results and not used_query:
            used_query = query

        for item in results:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            merged_results.append(item)
            seen_urls.add(url)
            if len(merged_results) >= max_results:
                return merged_results, used_query or query

    return merged_results, used_query or (queries[0] if queries else question)


async def search_web_by_queries(
    question: str,
    queries: list[str],
    max_results: int,
) -> tuple[list[dict], str]:
    """按指定搜索词执行联网搜索"""
    normalized_queries = []
    for item in queries:
        if not isinstance(item, str):
            continue
        query = re.sub(r"\s+", " ", item).strip()
        if query and query not in normalized_queries:
            normalized_queries.append(query)

    if not normalized_queries:
        return await search_web(question, max_results)

    merged_results = []
    seen_urls = set()
    used_query = ""

    for query in normalized_queries[:4]:
        results = await duckduckgo_search(query, max_results)
        if results and not used_query:
            used_query = query

        for item in results:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            merged_results.append(item)
            seen_urls.add(url)
            if len(merged_results) >= max_results:
                return merged_results, used_query or query

    return merged_results, used_query or normalized_queries[0]


def format_search_results(results: list[dict]) -> str:
    """把搜索结果整理成提示词上下文"""
    if not results:
        return "暂无搜索结果。"

    lines = []
    for index, item in enumerate(results, start=1):
        snippet = item.get("snippet") or "无摘要"
        lines.append(
            f"{index}. 标题：{item.get('title', '未知标题')}\n"
            f"   摘要：{snippet}\n"
            f"   链接：{item.get('url', '未知链接')}"
        )
    return "\n".join(lines)


def append_reference_links(answer: str, results: list[dict], max_links: int = 3) -> str:
    """在回答末尾追加明确的网址列表"""
    text = (answer or "").strip()
    links = []
    seen_urls = set()
    for item in results:
        url = (item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        title = clean_search_text(item.get("title", "") or "未命名来源")
        links.append((title, url))
        seen_urls.add(url)
        if len(links) >= max_links:
            break

    if not links:
        return text

    lines = [text, "", "参考网址："]
    for index, (title, url) in enumerate(links, start=1):
        lines.append(f"{index}. {title}")
        lines.append(url)
    return "\n".join(lines).strip()


def parse_tag_attributes(tag_text: str) -> dict[str, str]:
    """提取 HTML 标签属性"""
    attributes = {}
    for match in re.finditer(r'([-\w:]+)\s*=\s*["\']([^"\']+)["\']', tag_text or "", re.I):
        attributes[match.group(1).lower()] = html.unescape(match.group(2).strip())
    return attributes


def normalize_external_url(raw_url: str, page_url: str) -> str:
    """把页面中的相对资源地址转成绝对地址"""
    url = html.unescape((raw_url or "").strip())
    if not url or url.startswith("data:"):
        return ""
    normalized = urljoin(page_url, url)
    if normalized.startswith(("http://", "https://")):
        return normalized
    return ""


def extract_page_image_candidates(page_text: str, page_url: str) -> list[str]:
    """从 HTML 页面里提取适合预览的图片地址"""
    candidates = []
    seen_urls = set()

    for tag_text in re.findall(r"<meta[^>]+>", page_text or "", re.I):
        attrs = parse_tag_attributes(tag_text)
        marker = (attrs.get("property") or attrs.get("name") or "").lower()
        if marker not in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
            continue
        candidate = normalize_external_url(
            attrs.get("content") or attrs.get("value") or "",
            page_url,
        )
        if candidate and candidate not in seen_urls:
            candidates.append(candidate)
            seen_urls.add(candidate)

    for tag_text in re.findall(r"<link[^>]+>", page_text or "", re.I):
        attrs = parse_tag_attributes(tag_text)
        rel = (attrs.get("rel") or "").lower()
        if rel != "image_src":
            continue
        candidate = normalize_external_url(attrs.get("href", ""), page_url)
        if candidate and candidate not in seen_urls:
            candidates.append(candidate)
            seen_urls.add(candidate)

    return candidates


async def fetch_page_image_candidates(page_url: str) -> list[str]:
    """抓取页面并提取预览图"""
    headers = {
        "User-Agent": SEARCH_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    timeout = aiohttp.ClientTimeout(total=IMAGE_FETCH_TIMEOUT)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(page_url, headers=headers, allow_redirects=True) as response:
                if response.status != 200:
                    return []

                final_url = str(response.url)
                content_type = (response.headers.get("Content-Type") or "").lower()
                if content_type.startswith("image/") and "svg" not in content_type:
                    return [final_url]
                if content_type and "html" not in content_type:
                    return []

                page_text = await response.text(errors="ignore")
                return extract_page_image_candidates(page_text, final_url)
    except Exception as e:
        logs.warning(f"抓取页面预览图失败: {page_url} -> {e}")
        return []


async def collect_preview_images(
    results: list[dict],
    max_images: int = IMAGE_RESULT_LIMIT,
) -> list[dict]:
    """从搜索结果页提取可发送的预览图"""
    previews = []
    seen_urls = set()

    for item in results[:IMAGE_RESULT_SCAN_LIMIT]:
        source_url = (item.get("url") or "").strip()
        if not source_url:
            continue

        image_urls = await fetch_page_image_candidates(source_url)
        for image_url in image_urls:
            if image_url in seen_urls:
                continue
            previews.append(
                {
                    "image_url": image_url,
                    "title": item.get("title", "相关图片"),
                    "source_url": source_url,
                }
            )
            seen_urls.add(image_url)
            if len(previews) >= max_images:
                return previews

    return previews


def guess_image_extension(content_type: str, image_url: str) -> str:
    """根据响应头或 URL 选择保存后缀"""
    lowered_type = (content_type or "").lower()
    if "jpeg" in lowered_type or "jpg" in lowered_type:
        return ".jpg"
    if "png" in lowered_type:
        return ".png"
    if "webp" in lowered_type:
        return ".webp"

    suffix = Path(urlparse(image_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return ".jpg"


def build_preview_caption(previews: list[dict]) -> str:
    """构造图片预览说明"""
    lines = ["🖼️ 相关图片预览"]
    for index, item in enumerate(previews[:2], start=1):
        title = clean_search_text(item.get("title", "") or "相关来源")
        source_url = item.get("source_url", "")
        lines.append(f"{index}. {title}")
        if source_url:
            lines.append(source_url)
    return "\n".join(lines)[:900]


async def download_preview_image(
    session: aiohttp.ClientSession,
    image_url: str,
    target_dir: Path,
    index: int,
) -> Optional[Path]:
    """把预览图下载到临时目录"""
    try:
        async with session.get(
            image_url,
            headers={"User-Agent": SEARCH_USER_AGENT},
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                return None

            content_type = (response.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("image/") or "svg" in content_type:
                return None

            image_bytes = await response.read()
            if not image_bytes:
                return None

            path = target_dir / f"ais_preview_{index}{guess_image_extension(content_type, image_url)}"
            path.write_bytes(image_bytes)
            return path
    except Exception as e:
        logs.warning(f"下载预览图失败: {image_url} -> {e}")
        return None


async def send_preview_images(
    message: Message,
    bot: Client,
    previews: list[dict],
) -> None:
    """把搜索结果相关图片作为单图或图组发送出去"""
    if not previews:
        return

    chat_id = message.chat.id
    reply_to_message_id = message.id
    caption = build_preview_caption(previews)
    timeout = aiohttp.ClientTimeout(total=IMAGE_FETCH_TIMEOUT)

    with tempfile.TemporaryDirectory(prefix="ais_preview_") as tmp_dir:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            downloaded_paths = []
            for index, item in enumerate(previews, start=1):
                path = await download_preview_image(
                    session,
                    item.get("image_url", ""),
                    Path(tmp_dir),
                    index,
                )
                if path:
                    downloaded_paths.append(path)

        if not downloaded_paths:
            return

        if len(downloaded_paths) == 1:
            await bot.send_photo(
                chat_id=chat_id,
                photo=str(downloaded_paths[0]),
                caption=caption,
                reply_to_message_id=reply_to_message_id,
            )
            return

        media = [
            InputMediaPhoto(
                media=str(path),
                caption=caption if index == 0 else "",
            )
            for index, path in enumerate(downloaded_paths)
        ]
        await bot.send_media_group(
            chat_id=chat_id,
            media=media,
            reply_to_message_id=reply_to_message_id,
        )


def build_search_answer_messages(
    question: str,
    results: list[dict],
    search_query: str,
    intent: str = "",
) -> list[dict]:
    """构造带搜索上下文的对话消息"""
    context = format_search_results(results)
    intent_text = intent or "请先自行判断用户真正想知道的核心信息。"
    user_prompt = (
        f"用户问题：{question}\n\n"
        f"模型识别的用户核心意图：{intent_text}\n\n"
        f"本次使用的搜索词：{search_query}\n\n"
        f"搜索结果：\n{context}\n\n"
        "请直接回答用户最想知道的内容。"
    )
    return [
        {"role": "system", "content": SEARCH_ANSWER_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def build_direct_answer_messages(question: str) -> list[dict]:
    """构造普通问答消息"""
    return [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]


def format_search_fallback(
    results: list[dict], question: str, error_detail: str = ""
) -> str:
    """AI 调用失败时，返回搜索摘要兜底"""
    if not results:
        detail = error_detail or "请检查配置或网络连接"
        return f"❌ AI 回复获取失败\n\n{detail}"

    lines = [f"🌐 已联网搜索，但 AI 归纳失败。\n\n问题：{question}\n\n可参考线索："]
    if error_detail:
        lines.insert(1, f"接口反馈：{error_detail}")
    for index, item in enumerate(results[:3], start=1):
        snippet = item.get("snippet") or "无摘要"
        lines.append(
            f"{index}. {item.get('title', '未知标题')}\n"
            f"   {snippet}\n"
            f"   {item.get('url', '未知链接')}"
        )
    return "\n".join(lines)


def is_ai_success(result: Optional[str]) -> bool:
    """判断 AI 调用是否成功"""
    return bool(result and not is_retryable_ai_failure(result))


def summarize_model_failures(errors: list[tuple[str, str]]) -> str:
    """汇总多模型失败信息"""
    if not errors:
        return "❌ AI 回复获取失败\n\n请检查配置或网络连接"

    lines = ["❌ AI 回复获取失败", ""]
    for model, detail in errors[:3]:
        short_detail = (detail or "空响应").splitlines()[0][:120]
        lines.append(f"- {model}: {short_detail}")
    return "\n".join(lines)


async def request_ai_with_model_fallback(
    config: dict,
    messages: list[dict],
    preferred_model: str,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    extra_payload: Optional[dict] = None,
) -> tuple[Optional[str], str, list[tuple[str, str]]]:
    """按模型顺序请求 AI，失败时自动尝试备用模型"""
    all_models = config.get("models", [])
    ordered_models = [preferred_model]
    ordered_models.extend(
        model for model in all_models
        if isinstance(model, str) and model and model != preferred_model
    )

    errors = []
    for model in ordered_models:
        result = await call_ai_api(
            api_url=config["api_url"],
            api_key=config["api_key"],
            model=model,
            messages=messages,
            timeout=timeout,
            extra_payload=extra_payload,
        )
        if is_ai_success(result):
            return result, model, errors

        errors.append((model, result or "空响应"))
        logs.warning(f"模型请求失败，准备尝试下一个模型: {model} -> {result}")

    last_model = ordered_models[-1] if ordered_models else preferred_model
    last_error = errors[-1][1] if errors else "空响应"
    return last_error, last_model, errors


async def handle_search_command(message: Message, text: str):
    """处理搜索增强配置命令"""
    parts = text.split()
    action = parts[1].lower() if len(parts) > 1 else "status"

    config = load_config()
    search_config = get_search_config(config)

    if action in ("status", "show"):
        status = "开启" if search_config["enabled"] else "关闭"
        await message.edit(
            "🌐 搜索增强状态\n\n"
            f"开关：{status}\n"
            f"结果条数：{search_config['max_results']}\n\n"
            "命令示例：\n"
            "  ,ais search on\n"
            "  ,ais search off\n"
            "  ,ais search max 5"
        )
        return

    if action in ("on", "enable"):
        config["search"]["enabled"] = True
        if save_config(config):
            await message.edit("✅ 已开启搜索增强，事实类问题会先联网搜索再回答")
        else:
            await message.edit("❌ 保存搜索配置失败")
        await asyncio.sleep(3)
        await message.delete()
        return

    if action in ("off", "disable"):
        config["search"]["enabled"] = False
        if save_config(config):
            await message.edit("✅ 已关闭搜索增强，后续将直接调用模型回答")
        else:
            await message.edit("❌ 保存搜索配置失败")
        await asyncio.sleep(3)
        await message.delete()
        return

    if action == "max":
        if len(parts) < 3 or not parts[2].isdigit():
            await message.edit(
                "❌ 请提供 1-8 之间的数字\n\n"
                "示例：,ais search max 5"
            )
            await asyncio.sleep(3)
            await message.delete()
            return

        max_results = int(parts[2])
        if max_results < 1 or max_results > 8:
            await message.edit("❌ 搜索结果条数必须在 1-8 之间")
            await asyncio.sleep(3)
            await message.delete()
            return

        config["search"]["max_results"] = max_results
        if save_config(config):
            await message.edit(f"✅ 已将搜索结果条数设置为 {max_results}")
        else:
            await message.edit("❌ 保存搜索配置失败")
        await asyncio.sleep(3)
        await message.delete()
        return

    await message.edit(
        "❌ 未知的 search 子命令\n\n"
        "可用命令：\n"
        "  • ,ais search          - 查看搜索状态\n"
        "  • ,ais search on       - 开启搜索增强\n"
        "  • ,ais search off      - 关闭搜索增强\n"
        "  • ,ais search max <数> - 设置搜索结果条数"
    )
    await asyncio.sleep(4)
    await message.delete()


async def handle_url_command(message: Message, text: str):
    """处理 API URL 快捷配置命令"""
    config = load_config()
    parts = text.split(maxsplit=1)

    if len(parts) == 1 or not parts[1].strip():
        current_url = config.get("api_url")
        if current_url:
            await message.edit(f"🔗 当前 API URL：\n{current_url}")
        else:
            await message.edit(
                "⚠️ 当前还没有配置 API URL\n\n"
                "使用命令：,ais url <api_url>"
            )
        await asyncio.sleep(3)
        await message.delete()
        return

    raw_api_url = parts[1].strip()
    api_url = normalize_api_url(raw_api_url)
    config["api_url"] = api_url

    if save_config(config):
        api_key = config.get("api_key", "")
        current_model = get_current_model(config) or "未设置"
        key_text = mask_api_key(api_key) if api_key else "未设置"
        auto_fix_hint = ""
        if api_url != raw_api_url:
            auto_fix_hint = f"\n💡 已自动补全为: {api_url}"
        await message.edit(
            "✅ API URL 更新成功！\n\n"
            f"🔗 API URL: {api_url}\n"
            f"🔑 API Key: {key_text}\n"
            f"🤖 当前模型: {current_model}"
            f"{auto_fix_hint}"
        )
    else:
        await message.edit("❌ API URL 保存失败，请重试")

    await asyncio.sleep(3)
    await message.delete()
    return


async def decide_search_plan(
    api_url: str,
    api_key: str,
    model: str,
    question: str,
    search_config: dict,
) -> dict:
    """让模型决定是否搜索以及搜索什么"""
    if not search_config.get("enabled", True):
        return normalize_search_plan(question, search_config, {"use_search": False})

    planner_result = await call_ai_api(
        api_url=api_url,
        api_key=api_key,
        model=model,
        messages=build_search_router_messages(question),
        timeout=SEARCH_PLANNER_TIMEOUT,
        extra_payload={
            "temperature": 0,
            "max_tokens": 256,
        },
    )

    if not is_ai_success(planner_result):
        logs.warning("搜索路由模型调用失败，使用兜底规则")
        return normalize_search_plan(question, search_config)

    raw_plan = extract_json_object(planner_result)
    if raw_plan is None:
        logs.warning(f"搜索路由 JSON 解析失败: {planner_result}")
        return normalize_search_plan(question, search_config)

    return normalize_search_plan(question, search_config, raw_plan)


async def call_ai_api(
    api_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    extra_payload: Optional[dict] = None,
) -> Optional[str]:
    """调用AI API获取回复"""
    request_url = normalize_api_url(api_url)
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        def build_request_data() -> dict:
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
            }
            if isinstance(extra_payload, dict):
                payload.update(extra_payload)
            return payload

        async def parse_response(result: dict, retried: bool = False) -> Optional[str]:
            content = extract_ai_response_text(result)
            if content:
                return content

            if not retried and should_retry_empty_response(result):
                retry_data = build_request_data()
                retry_data.update(
                    {
                        "modalities": ["text"],
                        "response_format": {"type": "text"},
                        "reasoning_effort": "low",
                    }
                )
                logs.warning(
                    f"检测到空消息响应，尝试强制文本模式重试: endpoint={request_url}, model={model}"
                )
                async with session.post(
                    request_url, headers=headers, json=retry_data, timeout=timeout
                ) as retry_response:
                    if retry_response.status == 200:
                        retry_result = await retry_response.json()
                        retry_content = extract_ai_response_text(retry_result)
                        if retry_content:
                            return retry_content
                        result = retry_result
                    else:
                        retry_error_text = await retry_response.text()
                        retry_error_detail = clean_search_text(retry_error_text) or "无详细信息"
                        return (
                            "空消息重试失败\n"
                            f"接口：{request_url}\n"
                            f"状态：{retry_response.status}\n"
                            f"详情：{retry_error_detail[:200]}"
                        )

            first_choice = {}
            choices = result.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                first_choice = choices[0]

            logs.warning(
                "API响应解析失败，未提取到文本内容: "
                f"endpoint={request_url}, keys={list(result.keys())}, "
                f"choice_keys={list(first_choice.keys()) if first_choice else []}"
            )
            preview = build_response_debug_preview(result)
            return (
                "响应解析失败：接口已返回数据，但未找到可展示的文本字段。\n"
                f"接口：{request_url}\n"
                f"响应预览：{preview[:500]}"
            )

        data = build_request_data()

        async with aiohttp.ClientSession() as session:
            async with session.post(
                request_url, headers=headers, json=data, timeout=timeout
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return await parse_response(result)
                else:
                    error_text = await response.text()
                    error_detail = clean_search_text(error_text) or "无详细信息"
                    logs.error(
                        f"API调用失败: {response.status} - {error_detail} - endpoint: {request_url}"
                    )
                    return (
                        f"API调用失败: {response.status}\n"
                        f"接口：{request_url}\n"
                        f"详情：{error_detail[:200]}"
                    )
    except asyncio.TimeoutError:
        return f"请求超时（>{timeout}秒）"
    except Exception as e:
        logs.error(f"调用AI API异常: {e} - endpoint: {request_url}")
        return f"调用异常: {str(e)}\n接口：{request_url}"


@listener(command="ais", description="向AI模型提问", parameters="[文本]")
async def ais_query(message: Message, bot: Client):
    """处理AI查询命令"""
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
        mcp_status = "✅ 已启用" if HAS_MCP else "⚪ 未安装"

        help_text = f"""🤖 AI 查询插件帮助

📝 基础命令：
  ,ais <文本>              - 向AI提问
  ,ais help                - 显示此帮助

⚙️ API 配置：
  ,ais set <api_url> <api_key>  - 设置API基础配置
  ,ais url <api_url>            - 快速更新API地址（支持 base url）
  ,ais url                      - 查看当前API地址

🤖 模型管理：
  ,ais models              - 查看/切换模型
  ,ais model add <名称>    - 添加新模型
  ,ais model del <名称>    - 删除模型
  （当前模型异常时会自动尝试备用模型）

🌐 搜索增强：
  ,ais search              - 查看搜索增强状态
  ,ais search on           - 开启搜索增强
  ,ais search off          - 关闭搜索增强
  ,ais search max <数量>   - 设置搜索结果条数（1-8）

{'🔌 MCP 管理：' if HAS_MCP else ''}
{'  ,ais mcp list          - 列出所有MCP服务器' if HAS_MCP else ''}
{'  ,ais mcp add-raw <名称> <JSON>  - 添加MCP服务器（推荐，直接粘贴JSON）' if HAS_MCP else ''}
{'  ,ais mcp add <名称> <命令> [参数]  - 添加MCP服务器（简单）' if HAS_MCP else ''}
{'  ,ais mcp add-json <名称>  - 添加MCP服务器（回复方式）' if HAS_MCP else ''}
{'  ,ais mcp remove <名称>  - 删除MCP服务器' if HAS_MCP else ''}
{'  ,ais mcp enable <名称>  - 启用MCP服务器' if HAS_MCP else ''}
{'  ,ais mcp disable <名称> - 禁用MCP服务器' if HAS_MCP else ''}
{'  ,ais mcp tools         - 列出所有可用工具' if HAS_MCP else ''}

📌 MCP 状态：{mcp_status}

✨ 搜索增强补充：
  • 联网回答末尾会附上参考网址
  • 人物 / 项目 / 产品等实体查询会尽量补发相关图片预览

💡 使用示例：
  ,ais 今天天气怎么样
  ,ais 日本拖欠中国外包工资 是哪家公司
  ,ais cliproxyapi
  ,ais Taylor Swift 是谁
  ,ais search max 5
  ,ais url https://api.openai.com/v1
  ,ais set https://api.openai.com/v1/chat/completions sk-xxx
  ,ais model add gpt-3.5-turbo"""
        await message.edit(help_text)
        return

    # 检查是否是models命令
    if text.strip().lower() == "models":
        config = load_config()

        # 检查API配置是否存在
        if "api_url" not in config or "api_key" not in config:
            await message.edit(
                "⚠️ 请先配置API\n\n使用命令: ,ais set <api_url> <api_key>"
            )
            await asyncio.sleep(3)
            await message.delete()
            return

        # 获取所有模型
        models = config.get("models", [])
        current_model = get_current_model(config)

        if not models:
            # 如果没有模型，提示添加
            await message.edit(
                "📋 模型列表为空\n\n"
                "当前未添加任何模型，请使用以下命令添加：\n"
                ",ais model add <模型名称>\n\n"
                "示例: ,ais model add gpt-3.5-turbo"
            )
            await asyncio.sleep(5)
            await message.delete()
            return

        # 构建模型列表消息
        models_list = ""
        for i, model in enumerate(models, 1):
            if model == current_model:
                models_list += f"✅ **{i}. {model}** (当前使用)\n"
            else:
                models_list += f"   {i}. {model}\n"

        help_text = f"""🤖 模型列表

📋 可用模型：
{models_list}

💡 操作说明：
  • 切换模型: 回复此消息并输入序号
  • 添加模型: ,ais model add <模型名称>
  • 删除模型: ,ais model del <模型名称>

📌 回复消息输入 **1-9** 的序号快速切换模型"""

        sent_msg = await message.edit(help_text)

        # 记录待选择的消息
        chat_id = str(message.chat.id)
        PENDING_SELECTION[chat_id] = {
            "models": models,
            "message_id": sent_msg.id,
        }
        return

    # 检查是否是model子命令
    if text.strip().lower().startswith("model"):
        parts = text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else ""
        model_name = parts[2] if len(parts) > 2 else ""

        if action == "add":
            # 添加新模型
            if not model_name:
                await message.edit("❌ 请指定模型名称\n\n示例: ,ais model add gpt-4")
                await asyncio.sleep(3)
                await message.delete()
                return

            config = load_config()

            # 检查API配置是否存在
            if "api_url" not in config or "api_key" not in config:
                await message.edit(
                    "⚠️ 请先配置API\n\n使用命令: ,ais set <api_url> <api_key>"
                )
                await asyncio.sleep(3)
                await message.delete()
                return

            models = config.get("models", [])

            if model_name in models:
                await message.edit(f"⚠️ 模型 '{model_name}' 已存在")
                await asyncio.sleep(3)
                await message.delete()
                return

            models.append(model_name)
            config["models"] = models

            # 如果是第一个模型，自动设为当前模型
            if len(models) == 1:
                config["current_model"] = model_name

            if save_config(config):
                await message.edit(
                    f"✅ 成功添加模型: {model_name}\n\n"
                    f"📋 当前模型列表：\n" + "\n".join([f"  • {m}" for m in models])
                )
            else:
                await message.edit("❌ 保存配置失败")

            await asyncio.sleep(3)
            await message.delete()
            return

        elif action == "del" or action == "delete" or action == "rm":
            # 删除模型
            if not model_name:
                await message.edit(
                    "❌ 请指定要删除的模型名称\n\n示例: ,ais model del gpt-3.5-turbo"
                )
                await asyncio.sleep(3)
                await message.delete()
                return

            config = load_config()
            models = config.get("models", [])

            if model_name not in models:
                await message.edit(f"⚠️ 模型 '{model_name}' 不存在")
                await asyncio.sleep(3)
                await message.delete()
                return

            if len(models) <= 1:
                await message.edit("⚠️ 至少保留一个模型")
                await asyncio.sleep(3)
                await message.delete()
                return

            models.remove(model_name)
            config["models"] = models

            # 如果删除的是当前模型，切换到第一个
            if config.get("current_model") == model_name:
                config["current_model"] = models[0]

            if save_config(config):
                await message.edit(
                    f"✅ 已删除模型: {model_name}\n\n"
                    f"📋 当前模型列表：\n" + "\n".join([f"  • {m}" for m in models])
                )
            else:
                await message.edit("❌ 保存配置失败")

            await asyncio.sleep(3)
            await message.delete()
            return

        else:
            # 未知的model子命令
            await message.edit(
                "❌ 未知的model子命令\n\n"
                "可用命令：\n"
                "  • ,ais model add <名称> - 添加模型\n"
                "  • ,ais model del <名称> - 删除模型\n"
                "  • ,ais models - 通过序号选择模型"
            )
            await asyncio.sleep(3)
            await message.delete()
            return

    # 检查是否是 MCP 子命令
    if HAS_MCP and text.strip().lower().startswith("mcp"):
        await handle_mcp_command(message, text.strip())
        return

    # 检查是否是搜索增强配置命令
    if text.strip().lower().startswith("search"):
        await handle_search_command(message, text.strip())
        return

    # 检查是否是 API URL 快捷配置命令
    if text.strip().lower().startswith("url"):
        await handle_url_command(message, text.strip())
        return

    # 检查是否是配置命令
    if text.strip().lower().startswith("set"):
        # 提取配置参数
        parts = text.strip()[3:].strip().split()

        if len(parts) != 2:
            await message.edit(
                "❌ 配置格式错误\n\n"
                "正确格式: ,ais set <api_url> <api_key>\n\n"
                "示例: ,ais set https://api.openai.com/v1/chat/completions sk-xxx"
            )
            await asyncio.sleep(3)
            await message.delete()
            return

        raw_api_url, api_key = parts
        api_url = normalize_api_url(raw_api_url)

        # 加载现有配置
        config = load_config()

        # 保存API配置，保留现有的模型配置
        config["api_url"] = api_url
        config["api_key"] = api_key

        # 如果没有模型列表，使用model字段作为当前模型
        if "model" in config and "models" not in config:
            config["models"] = [config["model"]]
            config["current_model"] = config["model"]
            del config["model"]

        if save_config(config):
            current_model = get_current_model(config)
            auto_fix_hint = ""
            if api_url != raw_api_url:
                auto_fix_hint = f"💡 已自动补全为 chat/completions 接口：{api_url}\n"
            await message.edit(
                f"✅ API配置保存成功！\n\n"
                f"🔗 API URL: {api_url}\n"
                f"🔑 API Key: {mask_api_key(api_key)}\n"
                f"🤖 当前模型: {current_model}\n\n"
                f"{auto_fix_hint}"
                f"💡 使用 ,ais url <api_url> 可快速切换接口地址\n"
                f"💡 使用 ,ais model add <模型名> 添加更多模型"
            )
        else:
            await message.edit("❌ 配置保存失败，请重试")

        await asyncio.sleep(3)
        await message.delete()
        return

    # 加载配置
    config = load_config()

    # 检查API配置是否完整
    if "api_url" not in config or "api_key" not in config:
        await message.edit(
            "⚠️ 请先配置API\n\n"
            "首次配置使用：,ais set <api_url> <api_key>\n"
            "后续改地址可用：,ais url <api_url>"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    # 检查是否有模型配置
    models = config.get("models", [])
    if not models:
        await message.edit(
            "⚠️ 请先添加模型\n\n"
            "使用命令: ,ais model add <模型名>\n\n"
            "示例: ,ais model add gpt-3.5-turbo"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    # 调用AI API（优先尝试 MCP，如果可用）
    current_model = get_current_model(config)
    search_config = get_search_config(config)
    ui_config = get_ui_config(config)
    search_results = []
    search_query = ""
    preview_results = []
    search_plan = {
        "use_search": False,
        "intent": "",
        "reason": "",
        "search_queries": [],
        "need_images": False,
        "image_queries": [],
    }

    if search_config.get("enabled", True):
        await message.edit(
            f"🧭 正在分析问题并决定是否联网搜索...\n\n问题：{text}\n模型：{current_model}"
        )
        search_plan = await decide_search_plan(
            api_url=config["api_url"],
            api_key=config["api_key"],
            model=current_model,
            question=text,
            search_config=search_config,
        )

    if search_plan["use_search"]:
        await message.edit(
            f"🌐 正在联网搜索...\n\n问题：{text}\n"
            f"意图：{search_plan['intent'] or '自动识别'}\n"
            f"模型：{current_model}"
        )
        search_results, search_query = await search_web_by_queries(
            text,
            search_plan["search_queries"],
            search_config["max_results"],
        )

        if search_plan["need_images"]:
            preview_results = search_results
            if search_plan["image_queries"]:
                image_results, _ = await search_web_by_queries(
                    text,
                    search_plan["image_queries"],
                    IMAGE_RESULT_SCAN_LIMIT,
                )
                if image_results:
                    preview_results = image_results

    if search_results:
        await message.edit(
            f"🌐 已获取 {len(search_results)} 条搜索结果，正在整理回答...\n\n"
            f"问题：{text}\n"
            f"意图：{search_plan['intent'] or '自动识别'}\n"
            f"搜索词：{search_query}\n模型：{current_model}"
        )
        result, used_model, errors = await request_ai_with_model_fallback(
            config=config,
            preferred_model=current_model,
            messages=build_search_answer_messages(
                text,
                search_results,
                search_query,
                search_plan["intent"],
            ),
        )

        if is_ai_success(result):
            if used_model != current_model:
                logs.warning(f"AIS 已自动切换到备用模型回答: {current_model} -> {used_model}")
            rendered_result = append_reference_links(result, search_results)
            await message.edit(
                format_dialog_message(
                    text,
                    rendered_result,
                    ui_config["expand_ai_response"],
                )
            )
            if search_plan["need_images"]:
                previews = await collect_preview_images(preview_results or search_results)
                if previews:
                    await send_preview_images(message, bot, previews)
            return

        error_detail = summarize_model_failures(errors) if len(errors) > 1 else (result or "")
        await message.edit(format_search_fallback(search_results, text, error_detail))
        if search_plan["need_images"]:
            previews = await collect_preview_images(preview_results or search_results)
            if previews:
                await send_preview_images(message, bot, previews)
        return

    # 搜索没有命中时，尝试 MCP 增强
    mcp_result = None
    if HAS_MCP:
        try:
            client = get_mcp_client()
            if client and client.is_ready:
                await message.edit(
                    f"🔌 搜索结果不足，正在通过 MCP 补充处理...\n\n问题：{text}"
                )
                mcp_result = await client.smart_call(text)
        except Exception as e:
            logs.warning(f"MCP 调用失败，降级到 API: {e}")
            mcp_result = None

    if mcp_result:
        await message.edit(
            format_dialog_message(
                text,
                mcp_result,
                ui_config["expand_ai_response"],
            )
        )
        return

    await message.edit(f"🤖 正在向AI提问...\n\n问题：{text}\n\n模型：{current_model}")

    result, used_model, errors = await request_ai_with_model_fallback(
        config=config,
        preferred_model=current_model,
        messages=build_direct_answer_messages(text),
    )

    if is_ai_success(result):
        if used_model != current_model:
            logs.warning(f"AIS 已自动切换到备用模型回答: {current_model} -> {used_model}")
        await message.edit(
            format_dialog_message(
                text,
                result,
                ui_config["expand_ai_response"],
            )
        )
    else:
        await message.edit(summarize_model_failures(errors))


@listener(incoming=True, outgoing=True)
async def model_selection_handler(message: Message):
    """监听模型选择回复"""
    # 只处理回复消息
    if not message.reply_to_message:
        return

    chat_id = str(message.chat.id)

    # 检查是否有待处理的模型选择
    if chat_id not in PENDING_SELECTION:
        return

    selection_data = PENDING_SELECTION[chat_id]
    models = selection_data["models"]

    # 获取用户输入的序号
    user_text = (message.text or "").strip()

    # 只处理单数字符（1-9）
    if not user_text.isdigit() or len(user_text) != 1:
        return

    choice = int(user_text)

    if choice < 1 or choice > len(models):
        await message.reply_to_message.edit(
            f"❌ 无效序号，请输入 1-{len(models)} 之间的数字"
        )
        # 清理待选择状态
        del PENDING_SELECTION[chat_id]
        await message.delete()
        return

    # 获取选择的模型
    selected_model = models[choice - 1]
    current_model = get_current_model(load_config())

    # 如果选择的是当前模型
    if selected_model == current_model:
        await message.reply_to_message.edit(f"🤖 当前已是模型: **{selected_model}**")
        # 清理待选择状态
        del PENDING_SELECTION[chat_id]
        await message.delete()
        return

    # 更新配置
    config = load_config()
    config["current_model"] = selected_model

    if save_config(config):
        await message.reply_to_message.edit(
            f"✅ 已切换到模型: **{selected_model}**\n\n(原模型: {current_model})"
        )
    else:
        await message.reply_to_message.edit("❌ 切换失败")

    # 清理待选择状态
    del PENDING_SELECTION[chat_id]
    await message.delete()


# ============================================================================
# MCP 配置管理函数
# ============================================================================

def get_mcp_client() -> Optional[MCPClient]:
    """获取或创建 MCP 客户端实例"""
    global mcp_client, mcp_config_manager

    if not HAS_MCP:
        return None

    if mcp_client is None:
        mcp_config_manager = ConfigManager()
        mcp_client = MCPClient()

    return mcp_client


async def handle_mcp_command(message: Message, text: str):
    """处理 MCP 配置命令"""
    parts = text.split()
    action = parts[1].lower() if len(parts) > 1 else ""

    if action == "list":
        await mcp_list_servers(message)
    elif action == "add":
        await mcp_add_server(message, parts)
    elif action == "add-json":
        await mcp_add_server_json(message)
    elif action == "add-raw":
        # 新增：直接在命令后面粘贴 JSON
        await mcp_add_server_raw(message, text)
    elif action == "remove" or action == "rm" or action == "del":
        await mcp_remove_server(message, parts)
    elif action == "enable":
        await mcp_enable_server(message, parts)
    elif action == "disable":
        await mcp_disable_server(message, parts)
    elif action == "tools":
        await mcp_list_tools(message)
    elif action == "reload":
        await mcp_reload(message)
    elif action == "import":
        await mcp_import_config(message, parts)
    else:
        await mcp_show_help(message)


async def mcp_show_help(message: Message):
    """显示 MCP 帮助信息"""
    help_text = """🔌 MCP 配置管理

📝 可用命令：
  ,ais mcp list              - 列出所有MCP服务器
  ,ais mcp add-raw <名称> <JSON>  - 添加MCP服务器（推荐）
  ,ais mcp add <名称> <命令> [参数]  - 添加MCP服务器（简单）
  ,ais mcp add-json <名称>   - 添加MCP服务器（回复方式）
  ,ais mcp remove <名称>      - 删除MCP服务器
  ,ais mcp enable <名称>      - 启用MCP服务器
  ,ais mcp disable <名称>     - 禁用MCP服务器
  ,ais mcp tools             - 列出所有可用工具
  ,ais mcp reload            - 重新加载MCP配置
  ,ais mcp import <路径>     - 从文件导入配置

💡 配置示例：
  # 推荐方式（一条消息完成，支持环境变量）
  ,ais mcp add-raw minimax {"command":"uvx","args":["minimax-coding-plan-mcp","-y"],"env":{...}}

  # 简单方式（无环境变量）
  ,ais mcp add fs npx -y @modelcontextprotocol/server-filesystem /path

📌 配置文件：ais/mcp_config.json"""
    await message.edit(help_text)


async def mcp_list_servers(message: Message):
    """列出所有 MCP 服务器"""
    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    config_manager = client.config_manager
    servers = config_manager.list_servers()

    if not servers:
        await message.edit(
            "📋 MCP 服务器列表\n\n"
            "暂未配置任何MCP服务器\n\n"
            "使用 ,ais mcp add 添加服务器"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    # 构建服务器列表
    lines = ["📋 MCP 服务器列表\n\n"]
    for server in servers:
        status = "✅" if server["enabled"] else "⏸️"
        default = " (默认)" if server["is_default"] else ""
        type_emoji = {"stdio": "💻", "SSE": "🌐", "module": "📦"}.get(server["type"], "❓")

        lines.append(
            f"{status} **{server['name']}** {default}\n"
            f"   {type_emoji} 类型: {server['type']}"
        )

        # 显示配置详情（隐藏敏感信息）
        config = server["config"]
        if "command" in config:
            cmd = config["command"]
            args = " ".join(config.get("args", []))
            # 截断过长的命令
            if len(args) > 40:
                args = args[:37] + "..."
            lines.append(f"   命令: {cmd} {args}")
        elif "url" in config:
            url = config["url"]
            lines.append(f"   URL: {url}")

        lines.append("")

    await message.edit("\n".join(lines))


async def mcp_add_server(message: Message, parts: list):
    """添加 MCP 服务器"""
    if len(parts) < 4:
        await message.edit(
            "❌ 参数不足\n\n"
            "格式: ,ais mcp add <名称> <命令> [参数]\n\n"
            "示例:\n"
            "  ,ais mcp add fs npx -y @modelcontextprotocol/server-filesystem /path\n"
            "  ,ais mcp add search npx -y @modelcontextprotocol/server-brave-search\n\n"
            "如需设置环境变量，请使用:\n"
            "  ,ais mcp add-json <名称> <JSON配置>"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    name = parts[2]
    command = parts[3]
    args = parts[4:] if len(parts) > 4 else []

    # 检查是否是 URL 方式
    if command == "--url" and args:
        config = {"url": args[0], "enabled": True}
    else:
        config = {
            "command": command,
            "args": args,
            "enabled": True
        }

    config_manager = client.config_manager
    if config_manager.add_server(name, config):
        await message.edit(
            f"✅ 成功添加 MCP 服务器: {name}\n\n"
            f"命令: {command} {' '.join(args)}\n\n"
            f"使用 ,ais mcp list 查看所有服务器\n"
            f"使用 ,ais mcp tools 查看可用工具"
        )
    else:
        await message.edit("❌ 添加失败")

    await asyncio.sleep(3)
    await message.delete()


async def mcp_add_server_json(message: Message):
    """通过 JSON 添加 MCP 服务器（支持环境变量）"""
    # 检查是否回复了消息
    if not message.reply_to_message:
        await message.edit(
            "❌ 请回复包含 JSON 配置的消息\n\n"
            "格式: ,ais mcp add-json <名称>\n\n"
            "然后回复此消息，粘贴 JSON 配置:\n"
            "{\n"
            '  "command": "uvx",\n'
            '  "args": ["minimax-coding-plan-mcp", "-y"],\n'
            '  "env": {"KEY": "value"}\n'
            "}"
        )
        await asyncio.sleep(5)
        await message.delete()
        return

    # 检查回复的消息是否有文本内容
    reply_text = message.reply_to_message.text or ""
    if not reply_text.strip():
        await message.edit(
            "❌ 回复的消息不包含文本\n\n"
            "请回复包含 JSON 配置的文本消息"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    # 获取名称
    parts = message.text.split()
    if len(parts) < 3:
        await message.edit("❌ 请指定服务器名称\n\n格式: ,ais mcp add-json <名称>")
        await asyncio.sleep(3)
        await message.delete()
        return

    name = parts[2]

    # 解析 JSON
    try:
        json_text = reply_text
        # 提取 JSON（如果消息中有其他文本）
        import re
        json_match = re.search(r'\{[\s\S]*\}', json_text)
        if json_match:
            json_text = json_match.group(0)

        config = json.loads(json_text)
    except json.JSONDecodeError as e:
        await message.edit(f"❌ JSON 解析失败: {str(e)}\n\n请检查 JSON 格式是否正确")
        await asyncio.sleep(3)
        await message.delete()
        return
    except Exception as e:
        await message.edit(f"❌ 读取失败: {str(e)}")
        await asyncio.sleep(3)
        await message.delete()
        return

    # 验证必要字段
    if "command" not in config and "url" not in config:
        await message.edit("❌ 配置必须包含 command 或 url 字段")
        await asyncio.sleep(3)
        await message.delete()
        return

    # 添加 enabled 标记
    config["enabled"] = True

    # 保存配置
    config_manager = client.config_manager
    if config_manager.add_server(name, config):
        # 统计配置内容
        info_parts = []
        if "command" in config:
            info_parts.append(f"命令: {config['command']} {' '.join(config.get('args', []))}")
        if "url" in config:
            info_parts.append(f"URL: {config['url']}")
        if "env" in config:
            env_count = len(config["env"])
            info_parts.append(f"环境变量: {env_count} 个")

        await message.edit(
            f"✅ 成功添加 MCP 服务器: {name}\n\n"
            + "\n".join(info_parts)
            + f"\n\n使用 ,ais mcp list 查看所有服务器"
        )
    else:
        await message.edit("❌ 添加失败")

    await asyncio.sleep(3)
    await message.delete()


async def mcp_add_server_raw(message: Message, text: str):
    """直接在命令后面粘贴 JSON（最简单的方式）"""
    # 获取名称
    parts = text.split()
    if len(parts) < 3:
        await message.edit(
            "❌ 格式错误\n\n"
            "正确格式: ,ais mcp add-raw <名称> <JSON>\n\n"
            "示例:\n"
            ",ais mcp add-raw minimax {\"command\":\"uvx\",\"args\":[\"minimax-coding-plan-mcp\",\"-y\"],\"env\":{...}}"
        )
        await asyncio.sleep(5)
        await message.delete()
        return

    name = parts[2]

    # 提取 JSON（从名称后开始）
    import re
    # 查找第一个 { 及其之后的所有内容
    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        await message.edit(
            "❌ 未找到 JSON 配置\n\n"
            "请在命令后粘贴完整的 JSON 配置"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    json_text = json_match.group(0)

    # 解析 JSON
    try:
        config = json.loads(json_text)
    except json.JSONDecodeError as e:
        await message.edit(f"❌ JSON 解析失败: {str(e)}\n\n请检查 JSON 格式是否正确")
        await asyncio.sleep(3)
        await message.delete()
        return

    # 验证必要字段
    if "command" not in config and "url" not in config:
        await message.edit("❌ 配置必须包含 command 或 url 字段")
        await asyncio.sleep(3)
        await message.delete()
        return

    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    # 添加 enabled 标记
    config["enabled"] = True

    # 保存配置
    config_manager = client.config_manager
    if config_manager.add_server(name, config):
        # 统计配置内容
        info_parts = []
        if "command" in config:
            info_parts.append(f"命令: {config['command']} {' '.join(config.get('args', []))}")
        if "url" in config:
            info_parts.append(f"URL: {config['url']}")
        if "env" in config:
            env_count = len(config["env"])
            info_parts.append(f"环境变量: {env_count} 个")

        await message.edit(
            f"✅ 成功添加 MCP 服务器: {name}\n\n"
            + "\n".join(info_parts)
            + f"\n\n使用 ,ais mcp list 查看所有服务器"
        )
    else:
        await message.edit("❌ 添加失败")

    await asyncio.sleep(3)
    await message.delete()


async def mcp_remove_server(message: Message, parts: list):
    """删除 MCP 服务器"""
    if len(parts) < 3:
        await message.edit(
            "❌ 请指定要删除的服务器名称\n\n"
            "格式: ,ais mcp remove <名称>"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    name = parts[2]
    config_manager = client.config_manager

    if not config_manager.get_server(name):
        await message.edit(f"⚠️ 服务器 '{name}' 不存在")
        await asyncio.sleep(3)
        await message.delete()
        return

    if config_manager.remove_server(name):
        await message.edit(f"✅ 已删除 MCP 服务器: {name}")
    else:
        await message.edit("❌ 删除失败")

    await asyncio.sleep(3)
    await message.delete()


async def mcp_enable_server(message: Message, parts: list):
    """启用 MCP 服务器"""
    if len(parts) < 3:
        await message.edit(
            "❌ 请指定要启用的服务器名称\n\n"
            "格式: ,ais mcp enable <名称>"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    name = parts[2]
    config_manager = client.config_manager

    if config_manager.enable_server(name):
        await message.edit(f"✅ 已启用 MCP 服务器: {name}")
    else:
        await message.edit(f"⚠️ 服务器 '{name}' 不存在")

    await asyncio.sleep(3)
    await message.delete()


async def mcp_disable_server(message: Message, parts: list):
    """禁用 MCP 服务器"""
    if len(parts) < 3:
        await message.edit(
            "❌ 请指定要禁用的服务器名称\n\n"
            "格式: ,ais mcp disable <名称>"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    name = parts[2]
    config_manager = client.config_manager

    if config_manager.disable_server(name):
        await message.edit(f"✅ 已禁用 MCP 服务器: {name}")
    else:
        await message.edit(f"⚠️ 服务器 '{name}' 不存在")

    await asyncio.sleep(3)
    await message.delete()


async def mcp_list_tools(message: Message):
    """列出所有可用工具"""
    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    # 确保客户端已初始化
    await client.wait_ready(timeout=10)

    tools = client.list_tools(group_by_mcp=True)

    if not tools:
        await message.edit(
            "🔧 MCP 工具列表\n\n"
            "暂无可用工具\n\n"
            "请先添加并启用 MCP 服务器"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    # 构建工具列表
    lines = ["🔧 MCP 工具列表\n\n"]

    for group in tools:
        mcp_name = group["mcp_server"]
        tool_count = group["tool_count"]
        lines.append(f"📦 **{mcp_name}** ({tool_count} 个工具)")

        for tool in group["tools"][:10]:  # 每个 MCP 最多显示 10 个
            lines.append(f"  • {tool['name']}: {tool['description'][:50]}...")

        if tool_count > 10:
            lines.append(f"  ... 还有 {tool_count - 10} 个工具")

        lines.append("")

    await message.edit("\n".join(lines))


async def mcp_reload(message: Message):
    """重新加载 MCP 配置"""
    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    await message.edit("🔄 正在重新加载 MCP 配置...")

    success = await client.reload()

    if success:
        await message.edit("✅ MCP 配置已重新加载")
    else:
        await message.edit("⚠️ 未配置任何 MCP 服务器")

    await asyncio.sleep(3)
    await message.delete()


async def mcp_import_config(message: Message, parts: list):
    """从文件导入 MCP 配置"""
    if len(parts) < 3:
        await message.edit(
            "❌ 请指定配置文件路径\n\n"
            "格式: ,ais mcp import <文件路径>\n\n"
            "支持 VSCode/Claude Desktop 的 claude_desktop_config.json 格式"
        )
        await asyncio.sleep(3)
        await message.delete()
        return

    client = get_mcp_client()
    if not client:
        await message.edit("❌ MCP 模块未安装")
        await asyncio.sleep(3)
        await message.delete()
        return

    config_path = Path(parts[2])

    if not config_path.exists():
        await message.edit(f"⚠️ 文件不存在: {parts[2]}")
        await asyncio.sleep(3)
        await message.delete()
        return

    try:
        imported = client.config_manager.import_from_vscode_config(config_path)

        if imported:
            await message.edit(
                f"✅ 成功导入 {len(imported)} 个 MCP 服务器:\n\n"
                + "\n".join([f"  • {name}" for name in imported])
                + f"\n\n使用 ,ais mcp list 查看详情"
            )
        else:
            await message.edit("⚠️ 配置文件中未找到 MCP 服务器")

    except Exception as e:
        await message.edit(f"❌ 导入失败: {str(e)}")

    await asyncio.sleep(5)
    await message.delete()
