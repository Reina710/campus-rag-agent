# -*- coding: utf-8 -*-
"""
持久化记忆模块：让对话历史在程序重启后还在。

存的是"干净的问答"（user 问了什么、assistant 最终答了什么），用
json.dumps 写文件、json.loads 读回。存之前要先把 message 对象翻译成
纯字典，否则 json 序列化会失败。
"""

import json
import os
import config


# 记忆文件的位置（挂在项目根下）
MEMORY_FILE = os.path.join(config.PROJECT_ROOT, "data", "memory.json")


def _clean_message(m):
    """
    把一条 message 翻译成"能存进文件的纯字典"，只留 role / content。

    关键：tool 调用链不能跨进程持久化。role="tool" 的消息、带 tool_calls
    的 assistant 消息，都依赖 OpenAI 每次现生成的 tool_call_id，重启后这些
    id 就成了死链，模型反而看不懂这段历史。所以持久化时把这些中间产物丢掉，
    只留干净的问答。
    """
    role = m.get("role", "user")

    # 工具调用的中间产物，不存
    if role == "tool":
        return None
    if role == "assistant" and m.get("tool_calls"):
        return None

    content = m.get("content")
    if content is None or (isinstance(content, str) and not content.strip()):
        return None   # 空内容也不存

    return {
        "role": role,
        "content": content if isinstance(content, str) else str(content),
    }


def save(messages):
    """
    把整段对话历史存进文件。
    只存"干净问答"（user/assistant 的最终文本），不存 system 和 tool 中间过程。
    """
    # 跳过第 0 条 system（每次启动会重建），并滤掉 tool 中间产物
    body = []
    for m in messages[1:]:
        c = _clean_message(m)
        if c is not None:
            body.append(c)

    # 只保留最近 N 条，防止文件无限增长
    max_msgs = config.MEMORY_MAX_MESSAGES
    if len(body) > max_msgs:
        body = body[-max_msgs:]

    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)


def load():
    """
    读回历史记录。文件不存在或损坏时返回空列表（不崩，当没记忆）。
    """
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def clear():
    """清空记忆文件。"""
    if os.path.exists(MEMORY_FILE):
        os.remove(MEMORY_FILE)


def has_history():
    """有没有历史记录？"""
    return bool(load())


if __name__ == "__main__":
    # 直接运行本文件：存一轮再读回来，验证翻译是否正确
    fake = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    save(fake)
    print("存进去了，读回来：")
    print(json.dumps(load(), ensure_ascii=False, indent=2))
