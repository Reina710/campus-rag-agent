# -*- coding: utf-8 -*-
"""
语义题验证 —— 证明"字面题"和"语义题"会得出相反的结论。

背景：
    原来的题库题面用词和正确块的原文高度重合（"学硕""选课""盲审"…）。
    这类题的正确答案，几乎就是把问题里的词拿去和资料做字面匹配。
    结果：字频反而赢过向量 —— 这不代表字频更好，
          只代表这份题库没有区分力（Top-K 都是 100%，等于没测）。

这 5 道题故意"换一种说法"问同一件事，题面避开正确块里的关键词。
用来检验：字频（只见字）到底扛不扛得住"换个说法"。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import knowledge
import retriever as R

SEMANTIC = [
    # 题面里没有"选课""退课"，只有"报名""反悔"
    {"q": "报完名又不想上了，还能反悔吗？", "expect": "选课与学分"},
    # 题面里没有"实习""黄金期"，只有"攒实战经验"
    {"q": "我想早点出去攒点实战经验，哪段时间最合适？", "expect": "实习"},
    # 题面里没有"奖学金/助学金/申请"，只有"补助""办手续"
    {"q": "每个月发的那笔补助，什么时候去办手续？", "expect": "日常与生活"},
    # 题面里没有"秋招/校招/投递"，只有"进大公司""开始投"
    {"q": "想进大公司的话，从哪个时间点开始投比较合适？", "expect": "校招与求职"},
    # 题面里没有"校园卡/挂失"，只有"饭卡""找不到"
    {"q": "饭卡找不到了该怎么处理？", "expect": "日常与生活"},
]


def rank_of(hits, expect):
    for i, h in enumerate(hits, 1):
        if expect in h["block"]["title"]:
            return i
    return 0


def main():
    blocks = knowledge.load_blocks()
    top_k = 3

    print("=" * 78)
    print("  语义题验证（题面避开正确块的关键词）")
    print("=" * 78)
    print("%-32s %-10s %-10s %-10s" % ("问题", "字频排名", "BM25排名", "向量排名"))
    print("-" * 78)

    stat = {"freq": [0, 0.0], "bm25": [0, 0.0], "embed": [0, 0.0]}
    t0 = time.time()

    for it in SEMANTIC:
        line = {}
        for name, fn in (("freq", R.freq_search),
                         ("bm25", R.bm25_search),
                         ("embed", R.embed_search)):
            hits = fn(it["q"], blocks, top_k)
            r = rank_of(hits, it["expect"])
            line[name] = r
            if r == 1:
                stat[name][0] += 1
            if r > 0:
                stat[name][1] += 1.0 / r
        n = len(SEMANTIC)
        print("%-32s %-10s %-10s %-10s" % (
            it["q"][:30],
            ("第%d" % line["freq"]) if line["freq"] else "未中",
            ("第%d" % line["bm25"]) if line["bm25"] else "未中",
            ("第%d" % line["embed"]) if line["embed"] else "未中",
        ))

    print("-" * 78)
    for name, label in (("freq", "字频"), ("bm25", "BM25"), ("embed", "向量")):
        print("  %s：Top-1 命中 %d/%d = %.1f%%   MRR %.4f"
              % (label, stat[name][0], len(SEMANTIC),
                 stat[name][0] * 100.0 / len(SEMANTIC),
                 stat[name][1] / len(SEMANTIC)))
    print("=" * 78)
    print("  耗时 %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
