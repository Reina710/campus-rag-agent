# -*- coding: utf-8 -*-
"""
评测模块：拿一组标准问题跑一遍 Agent，量两个指标。

没有评测，这就是"又一个 RAG 问答"；有评测，就能拿出一组能复现的数字，
比如"切块从 300 改 800，命中率从 X% 变 Y%"。

工作方式：
    1. 拿一组标准问题，每题标好"答案应来自哪块资料"（expect）和"该出现的关键词"（keys）
    2. 让 Agent 挨个跑
    3. 量两个指标：检索命中（Top-K 里有没有正确块）、回答通过（回答里有没有关键词）
    4. 打一张表，逐题摊开，并落盘到 reports/

用法：python evaluate.py
"""

import os
import datetime
import config
from agent import KBAgent


# ------------------------------------------------------------
# 题库：每题三样东西
#     q       问题
#     expect  答案应来自哪块资料（对应 knowledge.md 的标题；支持列表=多块）
#     keys    回答里出现其中任意一个即算通过
#
# 题库分常规题和三类难题：
#     cross  跨块推理 —— 答案分散在多个块，需同时命中多块
#     trap   陷阱题   —— 知识库里没有答案，看它会不会编
#     inject 注入对抗 —— 问题里带攻击指令，看它会不会被劫持
#
# keys 两种形态：
#     普通：list，命中任意一个即通过（OR）
#     AND：list 里套 list，内层必须全中才算这一组过，外层之间取 OR
#          （用于跨块题"必须同时踩到两块"）
# ------------------------------------------------------------
# 题库本体在 question_bank.py（214 题，带难度分层），这里只是转出并保持
# `from evaluate import QUESTIONS` 这个老接口可用，旧脚本不用改。
from question_bank import QUESTIONS



def check_retrieval(hits, expect):
    """Top-K 里有没有该出现的资料。

    expect 兼容两种写法：
        字符串 -> 只有一个正确块
        列表   -> 多个正确块（跨块题），命中任意一个即算"检索命中"

    陷阱题的 expect 标"（无）"表示知识库里本就没答案，这类题在 run() 里
    单独处理，不按这里计检索分。
    """
    expects = [expect] if isinstance(expect, str) else list(expect)
    for h in hits:
        for e in expects:
            if e in h["block"]["title"]:
                return True, h["block"]["title"]
    return False, hits[0]["block"]["title"] if hits else "(什么都没捞到)"


def check_answer(answer, keys):
    """
    回答里有没有出现该出现的关键词。

    keys 两种形态：
        普通 list：命中任意一个即通过（OR）。
        list 里套 list：内层必须全中才算这一组过，各组之间取 OR
                       （用于跨块题"必须同时踩到两块"）。
    """
    hit = []
    for k in keys:
        if isinstance(k, list):
            # AND 组：这一组里的词必须全部出现
            if all(sub in answer for sub in k):
                hit.extend(k)
        else:
            if k in answer:
                hit.append(k)
    return len(hit) > 0, hit


def _classify(item):
    """给一道题归类：常规 / 跨块 / 陷阱 / 注入。"""
    return item.get("difficulty", "normal")


def run():
    # 评测时关掉两个交互开关：SHOW_THINKING 会把中间动作打到终端，
    # SUGGEST_FOLLOWUP 会在回答尾部加"猜你想问"，可能额外命中关键词污染判定。
    # agent/prompt 都是运行时读 config，这里改完立即生效。
    config.SHOW_THINKING = False
    config.SUGGEST_FOLLOWUP = False

    print("=" * 72)
    print("  评测开始")
    print("  检索器 = %s    切块 = %s    Top-K = %d   模式 = %s"
          % (config.RETRIEVER, config.CHUNK_MODE, config.TOP_K, config.AGENT_MODE))
    print("=" * 72)

    ok_retrieval = 0
    ok_answer = 0
    # 按难度分类统计
    stat = {}          # 动态统计：题里出现几种难度就统计几种

    rows = []   # 收集每行结果，落盘用

    header = "%-4s %-30s %-6s %-8s %s" % ("序号", "问题", "检索", "回答", "备注")
    print(header)
    rows.append(header)
    print("-" * 72)

    for i, item in enumerate(QUESTIONS):
        agent = KBAgent()
        answer = agent.ask(item["q"])

        r_ok, r_got = check_retrieval(agent.last_hits, item["expect"])
        a_ok, a_hit = check_answer(answer, item["keys"])

        # 陷阱题的特殊处理：如果知识库里本就没有答案（expect 为"（无）"），
        # 检索"命中"看的是"它有没有硬捞块来编"，这里不算进检索分。
        if item["expect"] == "（无）":
            r_ok = True   # 陷阱题不考检索，考"回答是否诚实"

        if r_ok:
            ok_retrieval += 1
        if a_ok:
            ok_answer += 1

        cls = _classify(item)
        stat.setdefault(cls, [0, 0])
        if r_ok:
            stat[cls][0] += 1
        if a_ok:
            stat[cls][1] += 1

        note = "捞到：%s" % r_got if not r_ok else "关键词：%s" % "、".join(a_hit)
        line = "%-4d %-30s %-6s %-8s %s" % (
            i + 1, item["q"][:22],
            "✓" if r_ok else "✗",
            "✓" if a_ok else "✗",
            note)
        print(line)
        rows.append(line)

    total = len(QUESTIONS)
    print("-" * 72)
    s1 = "检索命中率：%d/%d  = %.1f%%" % (ok_retrieval, total, ok_retrieval * 100.0 / total)
    s2 = "回答通过率：%d/%d  = %.1f%%" % (ok_answer, total, ok_answer * 100.0 / total)
    print(s1)
    print(s2)
    rows.append(s1)
    rows.append(s2)

    # 分类明细
    print("-" * 72)
    names = {"normal": "常规题", "cross": "跨块推理", "trap": "陷阱题",
             "inject": "注入对抗", "cross_pair": "易混淆"}
    for cls in ("normal", "cross", "cross_pair", "trap", "inject"):
        r, a = stat[cls]
        n = sum(1 for it in QUESTIONS if _classify(it) == cls)
        if n == 0:
            continue
        line = "  %s：%d 题，检索 %d/%d，回答 %d/%d" % (names[cls], n, r, n, a, n)
        print(line)
        rows.append(line)
    print("=" * 72)

    save_report(rows, ok_retrieval, ok_answer, total)


def save_report(rows, ok_retrieval, ok_answer, total):
    """把结果落盘到 reports/，文件名带时间戳，不会覆盖上次结果。"""
    os.makedirs("reports", exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("reports", "eval_%s_%s_k%d.txt"
                        % (stamp, config.RETRIEVER, config.TOP_K))
    with open(path, "w", encoding="utf-8") as f:
        f.write("检索器=%s 切块=%s Top-K=%d 模式=%s\n"
                % (config.RETRIEVER, config.CHUNK_MODE, config.TOP_K, config.AGENT_MODE))
        f.write("\n".join(rows))
        f.write("\n")
    print("\n[报告已写入 %s]" % path)


if __name__ == "__main__":
    run()
