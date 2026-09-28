# -*- coding: utf-8 -*-
"""
上下文压缩模块：对话太长时，不直接删，而是让模型把它压成摘要。

对比按 token 直接裁剪（trim，删最老的）：直接删会丢掉那一轮里可能还有用的
信息（比如用户前面说过"我是研二、专硕、想找算法实习"）。压缩的做法是
把旧对话交给模型，提炼出"后面还用得上的信息"压成一段短摘要，塞回 messages
顶部，再删掉原始旧对话。这样上下文变短了，关键信息没丢。
"""

import config
from openai import OpenAI

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.DEEPSEEK_KEY, base_url=config.DEEPSEEK_BASE_URL)
    return _client


# 让模型提炼摘要的提示词
COMPRESS_PROMPT = """下面是一段对话历史。请把它压缩成一段简短的摘要，
只保留对后续对话还可能有用的信息（用户是谁、说了什么需求、做了什么决定、你答应过什么）。
去掉寒暄、重复、和已经无关的内容。直接用中文输出摘要，不要加任何解释或前缀。"""


def compress_history(messages):
    """
    把一长串 messages 压成一段摘要文字。

    参数 messages：要压缩的对话历史（不含 system 那条）。
    返回：一段摘要字符串。
    """
    client = _get_client()

    # 把历史拼成可读文本
    text_parts = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if isinstance(content, str) and content.strip():
            text_parts.append("%s: %s" % (role, content))
    history_text = "\n".join(text_parts)

    if not history_text.strip():
        return ""

    resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": COMPRESS_PROMPT},
            {"role": "user", "content": history_text},
        ],
    )
    return resp.choices[0].message.content or ""


if __name__ == "__main__":
    # 直接运行会真调模型（花钱），所以这里只打印提示词，不真跑
    print("COMPRESS_PROMPT =")
    print(COMPRESS_PROMPT)
    print("\n提示：真跑 compress_history(...) 会调用 DeepSeek，花几分钱。")
