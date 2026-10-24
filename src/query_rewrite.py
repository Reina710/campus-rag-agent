# -*- coding: utf-8 -*-
"""
查询改写（Query Rewriting）：把用户原话改写成"检索友好"的 2-3 个版本。

为什么要这一层：
    用户的话不是为检索写的。"奖学金咋评的"里没有"评选标准"这个词；
    "它什么时候截止"里的"它"指谁，检索器不知道。直接拿原话去搜，
    命中率取决于"用户措辞刚好和知识库一致"这个巧合。

一次调用同时做两件事：
    1. 指代消解：把"它/这个/那个"换成上一轮的真实主语
    2. 多查询：生成 2-3 个不同措辞的检索版本，分别召回再合并

设计取舍：
    - 只在 config.QUERY_REWRITE 开着时才调用（每题多一次小模型调用，增加延迟和成本）
    - 改写失败/超时一律退回原问题，不能让"优化"把主流程弄挂
    - 返回的列表一定包含原问题（保底），最多 config.QUERY_REWRITE_MAX 条
"""

import config
from openai import OpenAI


_client = None


def _get_client():
    """惰性建客户端：没配 key 时 import 本模块不能崩。"""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.DEEPSEEK_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
            timeout=config.LLM_TIMEOUT,
            max_retries=1,      # 改写是锦上添花，失败就退原问题，不值得重试两次
        )
    return _client


SYSTEM = """你是检索查询改写器。把用户问题改写成 2-3 个适合检索的短查询。

要求：
1. 如果问题里有代词（它/这个/那个/该校/那里），根据【对话历史】替换成真实主语。
2. 保留原问题里的关键实体、数字、机构名。
3. 每个查询一行，只输出查询本身：不要编号、不要解释、不要引号。
4. 最多 3 行。问题已经很清楚时，原样输出一行。"""


def _recent_history(messages):
    """从 Agent 的 messages 里抽最近几轮"用户说 / 助手答"，供指代消解。"""
    if not messages:
        return ""
    lines = []
    for m in messages[-6:]:
        role = m.get("role")
        text = (m.get("content") or "").strip()
        if not text:
            continue
        if role == "user":
            lines.append("用户：" + text[:120])
        elif role == "assistant" and not m.get("tool_calls"):
            lines.append("助手：" + text[:120])
    return "\n".join(lines)


def rewrite(question, history=None):
    """把问题改写成若干检索查询。返回列表，第一个一定是原问题。

    history 是 Agent 的 messages 列表（可选）。有历史才能做指代消解。
    """
    question = (question or "").strip()
    if not question or not config.QUERY_REWRITE:
        return [question]

    hist = _recent_history(history)
    user = ""
    if hist:
        user += "【对话历史】\n%s\n\n" % hist
    user += "【用户问题】\n%s" % question

    try:
        resp = _get_client().chat.completions.create(
            model=config.CHAT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception:
        return [question]          # 失败就退回原问题，主流程不受影响

    out = []
    for line in text.splitlines():
        q = line.strip().strip("「」\"'“”‘’").strip()
        # 过滤掉标题行、"改写：" 这类非查询行，以及过短/过长的行
        if not q or q.startswith("【") or q.startswith("查询") or len(q) < 2 or len(q) > 60:
            continue
        if q not in out:
            out.append(q)

    # 保底：原问题必须在里面（改写跑偏时至少还搜原话）
    if question not in out:
        out.insert(0, question)

    return out[:config.QUERY_REWRITE_MAX]


if __name__ == "__main__":
    # 直接运行：看改写效果（需要 .env 里有 DEEPSEEK_KEY）
    import os
    print("QUERY_REWRITE =", config.QUERY_REWRITE)
    demo_hist = [
        {"role": "user", "content": "选课系统是哪个？"},
        {"role": "assistant", "content": "选课通过北京交通大学教学支撑平台进行，登录 MIS 系统后点教务系统。"},
    ]
    for q in ["它什么时候开放？", "奖学金咋评的？", "找实习"]:
        print("\n原问题：", q)
        print("改写：", rewrite(q, demo_hist))
