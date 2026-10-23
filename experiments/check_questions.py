# -*- coding: utf-8 -*-
"""
题库自检：跑评测前后都该跑一遍，防止"题库写错了却以为是检索器不行"。

检查四件事：
    1. 题数是否 150
    2. 每个 expect 能不能在知识库块标题里找到（找不到 = 这题永远判错，
       而且报错时不会提示，属于最典型的静默失败）
    3. expect 是否"只命中一块"（命中多块说明标注有歧义）
    4. level / difficulty 分布，确认难度梯度没写歪

用法：
    python experiments/check_questions.py
"""

import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

import knowledge
from question_bank import QUESTIONS


def main():
    blocks = knowledge.load_blocks()
    titles = [b["title"] for b in blocks]

    print("=" * 70)
    print("题库自检")
    print("=" * 70)
    print("知识库块数：%d   题目数：%d" % (len(blocks), len(QUESTIONS)))
    print()

    bad = []
    ambiguous = []

    for i, it in enumerate(QUESTIONS, 1):
        e = it["expect"]
        expects = [e] if isinstance(e, str) else list(e)
        if e == "（无）":
            continue                        # 陷阱题本来就没有正确块
        for one in expects:
            hit = [t for t in titles if one in t]
            if not hit:
                bad.append((i, it["q"], one))
            elif len(hit) > 1:
                ambiguous.append((i, it["q"], one, hit))

    if bad:
        print("!! expect 找不到对应块（这些题会永远判错）：")
        for i, q, one in bad:
            print("   #%d %s  ->  expect=%s" % (i, q[:28], one))
    else:
        print("[OK] 所有 expect 都能对上知识库块标题")

    if ambiguous:
        print("\n!! expect 命中多块（标注有歧义）：")
        for i, q, one, hit in ambiguous:
            print("   #%d %s  ->  %s  <= %s" % (i, q[:24], one, " / ".join(hit)))
    else:
        print("[OK] 每个 expect 只命中一块")

    # 覆盖情况：哪些块没有任何题
    covered = set()
    for it in QUESTIONS:
        e = it["expect"]
        if e == "（无）":
            continue
        for one in ([e] if isinstance(e, str) else list(e)):
            for t in titles:
                if one in t:
                    covered.add(t)
    missing = [t for t in titles if t not in covered]
    print("\n知识块覆盖：%d/%d" % (len(covered), len(titles)))
    if missing:
        print("  没被任何题覆盖的块：")
        for t in missing:
            print("   - %s" % t)

    print("\n难度层分布：%s" % dict(Counter(it.get("level", "?") for it in QUESTIONS)))
    print("题型分布：  %s" % dict(Counter(it.get("difficulty", "normal") for it in QUESTIONS)))

    # 跨块题：difficulty="cross" 的 expect 必须真的是列表
    # （cross_pair 是"两块易混淆"，expect 可以是单块——考的是排第一的对不对）
    wrong = [i for i, it in enumerate(QUESTIONS, 1)
             if it.get("difficulty") == "cross"
             and not isinstance(it["expect"], list)]
    if wrong:
        print("\n!! 标了 cross 但 expect 不是列表：%s" % wrong)

    print("=" * 70)
    return 1 if (bad or ambiguous) else 0


if __name__ == "__main__":
    sys.exit(main())
