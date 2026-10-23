# -*- coding: utf-8 -*-
"""复现：上下文压缩把 tool 调用对拆散，导致会话永久 400。

真实事故（190 题端到端跑陷阱题时）：
    前 5 题正常，第 6 题起每一题都返回
    "抱歉，这次没能答上来（服务暂时不可用）"。
    日志里的真实错误：
        Messages with role 'tool' must be a response to a preceding message
        with 'tool_calls'

根因链条：
    1) Agent 模式下一轮问答展开成四条消息
           user -> assistant(tool_calls) -> tool -> assistant(answer)
    2) trim_smart() 用 recent = messages[-2:] 保留"最新一轮"，
       但最后两条恰好是 [tool, assistant(answer)]，不是完整的一轮
    3) 压缩后 messages = [system, 摘要, tool, assistant]
       ——tool 前面是 system 摘要，没有 tool_calls
    4) 接口对序列有硬约束，之后每次请求都 400
    5) 坏状态只在内存里（memory.save 只存 user/assistant 文本），
       磁盘上看不出来，重启才恢复

这个脚本对比旧实现和新实现，并验证兜底清洗能救回坏序列。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import config  # noqa: E402
import token_estimator  # noqa: E402
from agent import KBAgent  # noqa: E402


def build_messages(rounds=3):
    """构造 rounds 轮完整的 Agent 对话（含 tool 往返）。"""
    msgs = [{"role": "system", "content": "你是助手"}]
    for i in range(1, rounds + 1):
        cid = "call_%d" % i
        msgs.append({"role": "user", "content": "第 %d 个问题" % i})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": cid, "type": "function",
            "function": {"name": "search_knowledge", "arguments": '{"query":"测试"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": cid, "content": "检索到的资料"})
        msgs.append({"role": "assistant", "content": "第 %d 个答案" % i})
    return msgs


def orphans(msgs):
    """孤立 tool 消息的下标（前面没有声明它的 assistant(tool_calls)）。"""
    pending = set()
    bad = []
    for i, m in enumerate(msgs):
        r = m.get("role")
        if r == "assistant" and m.get("tool_calls"):
            pending = {tc["id"] for tc in m["tool_calls"]}
        elif r == "tool":
            if m.get("tool_call_id") not in pending:
                bad.append(i)
            pending.discard(m.get("tool_call_id"))
        elif r == "user":
            pending = set()
    return bad


def roles(msgs):
    return "".join({"system": "S", "user": "U", "assistant": "A",
                    "tool": "T"}.get(m.get("role"), "?") for m in msgs)


def old_trim_smart(msgs, budget):
    """旧实现：用 [-2:] 保留最新一轮（不真调模型，假设压缩成功）。"""
    if token_estimator.estimate_messages(msgs) <= budget:
        return msgs
    if len(msgs) <= 3:
        while len(msgs) > 2 and token_estimator.estimate_messages(msgs) > budget:
            del msgs[1]
        return msgs
    old = msgs[1:-2]
    recent = msgs[-2:]
    assert old
    return [msgs[0], {"role": "system", "content": "（之前的对话摘要）"}] + recent


budget = 60
config.CONTEXT_MAX_TOKENS = budget
base = build_messages(3)

print("=" * 74)
print("上下文裁剪 / 压缩 对 tool 调用完整性的影响   token 预算 = %d" % budget)
print("=" * 74)
print("\n原始     %s   共 %d 条  %d token"
      % (roles(base), len(base), token_estimator.estimate_messages(base)))

old = old_trim_smart(build_messages(3), budget)
print("\n[旧] 压缩后  %s   共 %d 条" % (roles(old), len(old)))
print("     孤立 tool：%s  %s" % (orphans(old) or "无",
                                 "<-- 非法序列，之后每次请求都 400" if orphans(old) else ""))

a = KBAgent.__new__(KBAgent)
a.session_id = "repro"
a.messages = build_messages(3)
a.trim_smart()
print("\n[新] 压缩后  %s   共 %d 条" % (roles(a.messages), len(a.messages)))
print("     孤立 tool：%s  %s" % (orphans(a.messages) or "无",
                                 "<-- 非法" if orphans(a.messages) else "<-- 合法"))

a2 = KBAgent.__new__(KBAgent)
a2.session_id = "repro"
a2.messages = list(old)
dropped = a2._repair_tool_pairs()
print("\n[兜底] 坏序列交给 _repair_tool_pairs")
print("     丢弃 %d 条孤立 tool，修复后 %s，孤立：%s"
      % (dropped, roles(a2.messages), orphans(a2.messages) or "无"))

print("\n" + "=" * 74)
print("结论：旧实现 %s；新实现 %s；兜底 %s"
      % ("会产生非法序列" if orphans(old) else "本次未触发",
         "合法" if not orphans(a.messages) else "仍非法",
         "能救回" if not orphans(a2.messages) else "救不回"))
print("=" * 74)
