# -*- coding: utf-8 -*-
"""
CI 门禁：不联网、不花钱地跑一遍检索，指标低于阈值就让流水线失败。

为什么要有这一道：
    改了切块、BM25 参数、知识库内容之后，最容易发生的事是"某个指标悄悄掉了"。
    人不会每次都记得跑评测，但 CI 每次都会。阈值不是追求高分，是防退化。

只测 freq / bm25：这两路不依赖任何外部服务，CI 里不需要配密钥。
向量、RRF、rerank 三段要联网，放到本地用 src/eval_pipeline.py 跑。

用法：
    python experiments/ci_gate.py
退出码 0 = 通过，1 = 有指标低于阈值。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import knowledge
import retriever
from question_bank import QUESTIONS

# 阈值取当前实测值往下留 1~2 个点：只挡明显退化，不要求每次改动都涨。
THRESHOLDS = {
    "freq": 74.0,
    "bm25": 81.0,
}


def top1_rate(search_fn, blocks, items):
    hit = 0
    for it in items:
        expect = it["expect"]
        expects = [expect] if isinstance(expect, str) else list(expect)
        hits = search_fn(it["q"], blocks, 3)
        if hits and any(e in hits[0]["block"]["title"] for e in expects):
            hit += 1
    return hit / len(items) * 100


def main():
    blocks = knowledge.load_blocks()
    # 陷阱题没有正确答案，不参与检索指标
    items = [q for q in QUESTIONS if q["expect"] != "（无）"]
    print("知识库 %d 块 / 参与检索 %d 题" % (len(blocks), len(items)))

    results = {
        "freq": top1_rate(retriever.freq_search, blocks, items),
        "bm25": top1_rate(retriever.bm25_search, blocks, items),
    }

    failed = []
    for name, got in results.items():
        floor = THRESHOLDS[name]
        mark = "PASS" if got >= floor else "FAIL"
        print("  [%s] %-5s Top-1 %.1f%%  （阈值 %.1f%%）" % (mark, name, got, floor))
        if got < floor:
            failed.append("%s %.1f%% < %.1f%%" % (name, got, floor))

    if failed:
        print("\n门禁未通过：%s" % "; ".join(failed))
        return 1
    print("\n门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
