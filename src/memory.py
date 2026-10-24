# -*- coding: utf-8 -*-
"""
持久化记忆模块：让对话历史在程序重启后还在。

上线后一个进程要服务多个用户，记忆必须按会话分开存：
    data/memory/<session_id>.json
否则 A 的聊天记录会被 B 读出来（这是上线前的头号问题）。

安全：session_id 来自浏览器 cookie，属于不可信输入。这里只保留
字母/数字/下划线/短横，再截断长度，防止 "../../xxx" 这类值把文件
写到目录外面去。
"""

import json
import os
import re
import config


MEMORY_DIR = os.path.join(config.PROJECT_ROOT, "data", "memory")

# 白名单过滤：不在这个集合里的字符一律丢掉
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _path(session_id="default"):
    """会话 id -> 记忆文件路径。非法字符被剔除，空值退回 default。

    支持复合 key（浏览器 sid + 会话 conv，形如 "abc__conv1"）：
    这样同一个浏览器可以存多段对话，互不覆盖。
    """
    sid = _SAFE.sub("", str(session_id or ""))[:80] or "default"
    return os.path.join(MEMORY_DIR, sid + ".json")


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

    content = content if isinstance(content, str) else str(content)

    # 模型在答案尾部追加的"猜你想问"是展示用建议，不进历史——
    # 存进去的话，读回来是一段死文字，还会被当成答案的一部分。
    if role == "assistant":
        idx = content.find("猜你想问")
        if idx >= 0:
            content = content[:idx].rstrip()
        if not content:
            return None

    return {
        "role": role,
        "content": content,
    }


def save(messages, session_id="default"):
    """
    把整段对话历史存进"这个会话自己的"文件。
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

    path = _path(session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 先写临时文件再替换：进程被杀时不会留下半截 JSON（原子写）
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load(session_id="default"):
    """
    读回这个会话的历史。文件不存在或损坏时返回空列表（不崩，当没记忆）。
    """
    path = _path(session_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def clear(session_id="default"):
    """清空这个会话的记忆文件。"""
    path = _path(session_id)
    if os.path.exists(path):
        os.remove(path)


def has_history(session_id="default"):
    """这个会话有没有历史记录？"""
    return bool(load(session_id))


def list_conversations(sid):
    """列出某个浏览器（sid）下所有对话。返回 [{id, title, updated, turns}]。

    文件名约定：<sid>__<conv>.json。没有这个前缀的文件属于别的浏览器，不列。
    """
    if not os.path.isdir(MEMORY_DIR):
        return []
    prefix = _SAFE.sub("", str(sid or ""))[:80] + "__"
    out = []
    for name in os.listdir(MEMORY_DIR):
        if not name.startswith(prefix) or not name.endswith(".json"):
            continue
        conv = name[len(prefix):-len(".json")]
        path = os.path.join(MEMORY_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            updated = int(os.path.getmtime(path))
        except (OSError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        first = next((m.get("content") for m in data if m.get("role") == "user"), "")
        out.append({
            "id": conv,
            "title": (first or "新对话")[:24],
            "updated": updated,
            "turns": sum(1 for m in data if m.get("role") == "user"),
        })
    out.sort(key=lambda x: -x["updated"])
    return out


def purge_older_than(days=7):
    """清理很久没动过的记忆文件（部署环境的日常运维用）。

    不清理的话，每个访客都会留下一个文件，磁盘只涨不降。
    返回删掉的文件数。
    """
    import time
    if not os.path.isdir(MEMORY_DIR):
        return 0
    deadline = time.time() - days * 86400
    removed = 0
    for name in os.listdir(MEMORY_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(MEMORY_DIR, name)
        try:
            if os.path.getmtime(path) < deadline:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


if __name__ == "__main__":
    # 直接运行本文件：存一轮再读回来，验证分会话隔离是否生效
    fake = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    save(fake, "selftest-a")
    print("a 会话读回：", json.dumps(load("selftest-a"), ensure_ascii=False))
    print("b 会话读回：", json.dumps(load("selftest-b"), ensure_ascii=False))
    clear("selftest-a")
    print("清理后 a 会话：", load("selftest-a"))
