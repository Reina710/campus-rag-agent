# -*- coding: utf-8 -*-
"""
Token 估算模块：大概算出一段话要占多少 token。

为什么需要它：模型计费和上下文窗口都按 token 算，不是按"几条消息"算。
一条 3000 字的消息可能顶得上十条 30 字的消息，所以裁剪历史时应该按
token 预算来裁，而不是按条数裁。

为什么是"估算"而非"精算"：精确的 token 数要加载分词器词表（如 tiktoken）。
这里用够用的经验公式判断"是否超了、该不该裁"：
    - 中文：一个字约 1.5~2 个 token（中文字符密度高）
    - 英文/数字：约 4 个字符折 1 个 token（英文单词平均 4 字母）
"""


def estimate_tokens(text):
    """
    估算一段文字的 token 数。

    规则（经验公式，非精确分词）：
        每个中文字符（含中文标点）算 2 个 token；
        其余字符（英文、数字、空格、换行）约 4 个字符折 1 个 token。
    """
    if not text:
        return 0

    cjk = 0      # 中文字符数
    other = 0    # 非中文字符数
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            cjk += 1
        else:
            other += 1

    # 中文 × 2，其余 ÷ 4
    return cjk * 2 + (other + 3) // 4


def estimate_messages(messages):
    """
    估算一整个 messages 列表的总 token 数。
    每条消息 = 内容 token + 每条固定 4 个 token 的结构开销（role 等）。
    """
    total = 0
    for m in messages:
        total += 4
        content = m.get("content") or ""
        if isinstance(content, str):
            total += estimate_tokens(content)
        # tool_calls 里的 arguments 也是文字，粗略计入
        for tc in m.get("tool_calls", []) or []:
            total += estimate_tokens(tc["function"].get("arguments", ""))
            total += estimate_tokens(tc["function"].get("name", ""))
    return total


if __name__ == "__main__":
    # 直接运行本文件可查看估算结果
    samples = [
        "你好",
        "你好，请问选课时间是什么？",
        "Hello, world!",
        "选课一般在开学前一周到开学后两周内完成。",
        "1234567890",
        "",
    ]
    for s in samples:
        print("%-40s -> %d tokens" % (repr(s), estimate_tokens(s)))
