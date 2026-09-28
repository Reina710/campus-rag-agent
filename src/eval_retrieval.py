# -*- coding: utf-8 -*-
"""
检索层评测 —— 只量"检索"，不跑 LLM 生成。

为什么单独一个脚本？
    evaluate.py 把"检索"和"回答"绑在一起：想量检索命中率，
    得先把全部题走完 Agent + DeepSeek 生成。慢、贵、还混入生成的不确定性。
    量检索就该只跑检索。

对比对象（四方）：
    freq   字频检索（不花钱）
    bm25   分词 + IDF 检索（不花钱，jieba）
    embed  向量检索（要联网，走阿里云）
    rrf    bm25 + embed 双路融合（要联网）

指标（从"命中率"升级到"三个维度"）：
    Top-1 命中率  正确块是不是就排第一
    Top-K 命中率  前 K 个里有没有正确块（至少一个）
    recall@K      所有正确块里，进了 Top-K 的有几个 / 总共几个
                   —— 命中率只问"有没有"，recall 问"捞回了几个"，更细
    MRR           平均倒数排名 —— 命中越靠前分越高，没命中记 0
                    1.0 = 每次都排第一；0.5 = 平均排在第二附近

    ★ 命中率 vs recall@K 的区别：
        一道题有 2 个正确块，你只捞回 1 个：
          命中率：算"命中"（有一个就算）
          recall@K：只有 1/2 = 50%（漏了另一半）
        所以命中率容易"虚高"，recall@K 才能暴露"捞不全"——这正是跨块题的病根。

expect 的两种写法（对应 evaluate.py 里的题库）：
    "标题"          → 这道题只有一个正确块
    ["标题A", "标题B"] → 这道题有多个正确块（跨块题），recall@K 才有意义

用法：
    python eval_retrieval.py
"""

import os
import sys
import time
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import knowledge
import retriever as R
from evaluate import QUESTIONS

TOP_K = config.TOP_K


def _expects(item):
    """把 expect 归一成列表（兼容单字符串和列表两种写法）。"""
    e = item["expect"]
    if isinstance(e, str):
        return [e]
    return list(e)


def _match(hits, expect):
    """一个正确块在 Top-K 结果里的排名；不在返回 0。"""
    for i, h in enumerate(hits, 1):
        if expect in h["block"]["title"]:
            return i
    return 0


def run_one(name, fn):
    blocks = knowledge.load_blocks()

    n = 0
    hit1 = 0          # 有多少题：正确块排第一
    hitk = 0          # 有多少题：至少捞回一个正确块
    recall_sum = 0.0  # 累加每题的 recall@K（捞回几个 / 总共几个）
    mrr = 0.0
    api_calls = 0
    t_all = time.time()
    rows = []

    for it in QUESTIONS:
        # 陷阱题的 expect 是"（无）"，本就不存在"正确块"，不考检索
        if it["expect"] == "（无）":
            continue
        n += 1
        expects = _expects(it)      # 这道题有几个正确块
        t0 = time.time()
        hits = fn(it["q"], blocks, TOP_K)
        cost = time.time() - t0

        # 每个正确块各自的排名（0 = 没进 Top-K）
        ranks = [_match(hits, e) for e in expects]
        got = [r for r in ranks if r > 0]      # 捞回的正确块排名
        got_count = len(got)
        total_count = len(expects)

        # 三个指标
        if got and ranks[0] == 1:              # 第一正确块排第一
            hit1 += 1
        if got_count > 0:                      # 至少捞回一个
            hitk += 1
        recall_sum += got_count / total_count  # recall@K = 捞回/总共
        if got:
            mrr += 1.0 / min(got)              # 取"最靠前"的那个算 MRR

        rows.append((it["q"], expects, total_count, got_count,
                     [h["block"]["title"] for h in hits], ranks, cost))

    used = time.time() - t_all
    return {
        "name": name, "n": n, "hit1": hit1, "hitk": hitk,
        "recall": recall_sum / n if n else 0.0,
        "mrr": mrr / n if n else 0.0, "used": used,
        "api_calls": api_calls, "rows": rows,
        "avg": used / n if n else 0.0,
    }


def show(res):
    print("=" * 76)
    print("  检索器 = %-6s   题目数 = %d   Top-K = %d   块数 = %d"
          % (res["name"], res["n"], TOP_K, len(knowledge.load_blocks())))
    print("=" * 76)
    print("%-3s %-24s %-6s %-8s %s" % ("#", "问题", "命中", "recall", "Top-K 捞到的块"))
    print("-" * 76)
    for i, (q, expects, total, got, titles, ranks, cost) in enumerate(res["rows"], 1):
        mark = "第%d" % min(ranks) if got else "未中"
        rc = "%d/%d" % (got, total)
        print("%-3d %-24s %-6s %-8s %s" % (i, q[:22], mark, rc, " | ".join(titles)))
    print("-" * 76)
    print("  Top-1 命中：%2d/%d = %6.1f%%" % (res["hit1"], res["n"], res["hit1"] * 100.0 / res["n"]))
    print("  Top-K 命中：%2d/%d = %6.1f%%" % (res["hitk"], res["n"], res["hitk"] * 100.0 / res["n"]))
    print("  recall@%d  ：%.4f" % (TOP_K, res["recall"]))
    print("  MRR       ：%.4f" % res["mrr"])
    print("  总耗时    ：%.1fs（平均每题 %.2fs）" % (res["used"], res["avg"]))
    print()


def main():
    # 跑哪些检索器？embed/rrf 要联网花钱，freq/bm25 免费。
    # 默认全跑，拿完整对比。
    results = []

    print("\n>>> 跑字频检索（不花钱）...")
    freq = run_one("freq", R.freq_search)
    results.append(freq)
    show(freq)

    print(">>> 跑 BM25 检索（jieba 分词 + IDF，不花钱）...")
    bm25 = run_one("bm25", R.bm25_search)
    results.append(bm25)
    show(bm25)

    print(">>> 跑向量检索（要联网，会真调阿里云接口）...")
    embed = run_one("embed", R.embed_search)
    results.append(embed)
    show(embed)

    print(">>> 跑 RRF 融合（bm25 + embed 双路，要联网）...")
    rrf = run_one("rrf", R.rrf_search)
    results.append(rrf)
    show(rrf)

    print("=" * 76)
    print("  对比结论")
    print("=" * 76)
    print("  %-8s %-10s %-10s %-10s %s" % ("检索器", "Top-1", "Top-K", "recall@K", "MRR"))
    for r in results:
        print("  %-8s %6.1f%%   %6.1f%%   %7.4f   %.4f"
              % (r["name"], r["hit1"] * 100.0 / r["n"], r["hitk"] * 100.0 / r["n"],
                 r["recall"], r["mrr"]))
    print("=" * 76)

    # 落盘
    os.makedirs("reports", exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("reports", "retrieval_%s.txt" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        f.write("检索层四方对比  Top-K=%d  块数=%d\n" % (TOP_K, len(knowledge.load_blocks())))
        f.write("%-8s %-8s %-8s %-9s %s\n" % ("检索器", "Top-1", "Top-K", "recall@K", "MRR"))
        for r in results:
            f.write("%-8s %5.1f%%   %5.1f%%   %6.4f   %.4f\n"
                    % (r["name"], r["hit1"] * 100.0 / r["n"], r["hitk"] * 100.0 / r["n"],
                       r["recall"], r["mrr"]))
        f.write("\n=== 逐题明细 ===\n")
        for r in results:
            f.write("\n[%s]\n" % r["name"])
            for i, (q, expects, total, got, titles, ranks, cost) in enumerate(r["rows"], 1):
                f.write("%2d. %s\n    期望块=%s  捞回=%d/%d  排名=%s\n    捞到=%s\n"
                        % (i, q, " | ".join(expects), got, total,
                           ",".join(str(x) for x in ranks) or "未中",
                           " | ".join(titles)))
    print("\n[报告已写入 %s]" % path)


if __name__ == "__main__":
    main()
