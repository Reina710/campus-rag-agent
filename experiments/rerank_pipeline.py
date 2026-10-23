# -*- coding: utf-8 -*-
"""
rerank 流水线对照实验：验证「宽召回 -> RRF 合并 -> rerank 精排」是否比现状好。

现状（retriever.py）：
    两路各自排全量 -> RRF 算分 -> 直接取 Top-3 当最终顺序
    问题：RRF 只看名次不看内容，打平后由文档顺序决定胜负（#18/#27）

本脚本对比四组：
    A  embed 单路 Top-3          现状最强基线（96.3%）
    B  等权 RRF 全量 Top-3        现状融合（92.6%）
    C  宽召回 + RRF 合并 Top-3     加了候选池，但最终顺序仍由 RRF 定
    D  宽召回 + RRF 合并 + rerank  最终顺序交给能读内容的模型

怎么判定「排错了」：
    题库里每题都有 expect（人工标注的正确答案块标题）。
    Top-3 里第一个 title 包含 expect 的，名次就是「正确块的名次」。
    名次不是 1，就是排错了。没有 expect 就永远不知道对错——这是评测集的作用。

用法：
    python experiments/rerank_pipeline.py
    python experiments/rerank_pipeline.py --limit 5     # 先跑 5 题试水

依赖：ca-test venv（jieba + dashscope），需要 .env 里的 DASHSCOPE_KEY。
"""

import os
import sys
import time

# 脚本在 experiments/ 下，源码在 ../src，需要先把 src 加进 import 路径
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

import config
import knowledge
import retriever
from evaluate import QUESTIONS
from dashscope import TextReRank


# ------------------------------------------------------------
# 参数：三个数字决定整条链路
# ------------------------------------------------------------
RECALL_K = 20      # 每路宽召回多少条（要够宽，rerank 才有东西可选）
RERANK_N = 10      # 送进 rerank 的候选数（多了慢，少了可能漏）
FINAL_K = config.TOP_K    # 最终喂给模型的条数，项目里是 3
RRF_K = 60         # RRF 平滑常数

RERANK_MODEL = "gte-rerank-v2"


def expects_of(item):
    """把 expect 统一成列表（跨块题本来就是列表）。"""
    e = item["expect"]
    return [e] if isinstance(e, str) else list(e)


def is_trap(item):
    """陷阱题：知识库里本就没答案，不计检索指标（与项目口径一致）。"""
    return item["expect"] == "（无）"


def hit_pos(hits, item):
    """正确块在 hits 里排第几（1 起）；没命中返回 None。"""
    ex = expects_of(item)
    for i, h in enumerate(hits, 1):
        t = h["block"]["title"]
        if any(e in t for e in ex):
            return i
    return None


def recall_at_k(hits, item, k):
    """该找的块，前 k 条里覆盖了几个（跨块题才有意义）。"""
    ex = expects_of(item)
    got = set()
    for h in hits[:k]:
        t = h["block"]["title"]
        for e in ex:
            if e in t:
                got.add(e)
    return len(got) / len(ex)


def metrics(rows):
    """rows: [(item, hits)] -> Top-1 / Top-3 / recall@3 / MRR"""
    n = len(rows)
    top1 = top3 = 0
    mrr = 0.0
    rc = 0.0
    for item, hits in rows:
        p = hit_pos(hits, item)
        if p == 1:
            top1 += 1
        if p is not None and p <= FINAL_K:
            top3 += 1
            mrr += 1.0 / p
        rc += recall_at_k(hits, item, FINAL_K)
    return {
        "n": n,
        "top1": top1 / n,
        "top3": top3 / n,
        "recall": rc / n,
        "mrr": mrr / n,
    }


# ------------------------------------------------------------
# 一次查询只算一次向量：全量排好，各处切片复用
# ------------------------------------------------------------
def full_rankings(question, blocks):
    """返回 (bm25 全量排名, embed 全量排名)。两路都排全量，后面想取几条都行。"""
    bm = retriever.bm25_search(question, blocks, len(blocks))
    em = retriever.embed_search(question, blocks, len(blocks))
    return bm, em


def rrf_from(bm, em, candidates, k):
    """在给定候选集上按 RRF 分排序。

    candidates 是块列表；不在某一路排名里的块（宽召回时可能发生）按
    「该路最后一名再往后一位」计名次，贡献极小但不为零。
    """
    r_bm = {h["block"]["id"]: i for i, h in enumerate(bm, 1)}
    r_em = {h["block"]["id"]: i for i, h in enumerate(em, 1)}
    far = max(len(bm), len(em)) + 1
    scored = []
    for b in candidates:
        s = 1.0 / (r_bm.get(b["id"], far) + RRF_K) + 1.0 / (r_em.get(b["id"], far) + RRF_K)
        scored.append({"block": b, "score": s})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


# ------------------------------------------------------------
# rerank：把 (问题, 候选块) 成对送进去，拿相关性分重排
# ------------------------------------------------------------
def rerank(question, cands):
    if not cands:
        return []

    # 文档里必须带标题：这两道错题的差别就写在标题上
    # 「奖学金评选标准」 vs 「奖学金类型」，只给正文反而更难分
    docs = ["标题：%s\n正文：%s" % (c["block"]["title"], c["block"]["text"]) for c in cands]

    resp = TextReRank.call(
        model=RERANK_MODEL,
        query=question,
        documents=docs,
        top_n=len(docs),
        return_documents=False,
        api_key=config.DASHSCOPE_KEY,
    )
    if resp.status_code != 200:
        raise RuntimeError("rerank 调用失败: %s %s" % (resp.status_code, resp))

    # results 里带 index，按原顺序回填分数
    score_of = {r["index"]: r["relevance_score"] for r in resp.output["results"]}
    scored = [{"block": c["block"], "score": score_of.get(i, 0.0)} for i, c in enumerate(cands)]
    scored.sort(key=lambda x: -x["score"])
    return scored


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    blocks = knowledge.load_blocks()
    items = [q for q in QUESTIONS if not is_trap(q)]
    if limit:
        items = items[:limit]

    print("=" * 78)
    print("rerank 流水线对照实验")
    print("=" * 78)
    print("知识库 %d 块 | 题目 %d 道（已排除陷阱题）" % (len(blocks), len(items)))
    print("宽召回 RECALL_K=%d | 送 rerank %d 条 | 最终取 %d 条" % (RECALL_K, RERANK_N, FINAL_K))
    print()

    rows_a, rows_b, rows_c, rows_d = [], [], [], []
    detail = []

    for idx, item in enumerate(items, 1):
        q = item["q"]
        t0 = time.time()
        bm, em = full_rankings(q, blocks)

        # A: 单路向量
        a = em[:FINAL_K]

        # B: 全量 RRF（复现现状）
        b = rrf_from(bm, em, blocks, FINAL_K)

        # C: 宽召回池 -> RRF 合并 -> 直接取 3
        pool_ids = {h["block"]["id"] for h in bm[:RECALL_K]} | {h["block"]["id"] for h in em[:RECALL_K]}
        pool = [x for x in blocks if x["id"] in pool_ids]
        merged = rrf_from(bm, em, pool, RERANK_N)
        c = merged[:FINAL_K]

        # D: 同一份候选 -> rerank 重排 -> 取 3
        d = rerank(q, merged)[:FINAL_K]

        rows_a.append((item, a))
        rows_b.append((item, b))
        rows_c.append((item, c))
        rows_d.append((item, d))

        detail.append({
            "no": idx,
            "q": q,
            "expect": "/".join(expects_of(item)),
            "a": hit_pos(a, item),
            "b": hit_pos(b, item),
            "c": hit_pos(c, item),
            "d": hit_pos(d, item),
            "top_b": [h["block"]["title"] for h in b],
            "top_d": [h["block"]["title"] for h in d],
        })
        print("  [%2d/%2d] %-28s A=%-4s B=%-4s C=%-4s D=%-4s  (%.1fs)" % (
            idx, len(items), q[:26],
            detail[-1]["a"] or "x", detail[-1]["b"] or "x",
            detail[-1]["c"] or "x", detail[-1]["d"] or "x",
            time.time() - t0,
        ))

    # ---------------- 汇总 ----------------
    print()
    print("=" * 78)
    print("汇总（正确块名次；x = 前 %d 条里没有）" % FINAL_K)
    print("=" * 78)
    names = [
        ("A 单路向量", rows_a),
        ("B 全量 RRF", rows_b),
        ("C 宽召回+RRF", rows_c),
        ("D 宽召回+RRF+rerank", rows_d),
    ]
    print("%-22s %8s %8s %10s %8s" % ("方案", "Top-1", "Top-3", "recall@3", "MRR"))
    print("-" * 60)
    for name, rows in names:
        m = metrics(rows)
        print("%-22s %7.1f%% %7.1f%% %10.3f %8.3f" % (
            name, m["top1"] * 100, m["top3"] * 100, m["recall"], m["mrr"]))

    # ---------------- 谁变了 ----------------
    print()
    print("=" * 78)
    print("逐题变化（只列 A/B/D 有任何一处不同的题）")
    print("=" * 78)
    for d in detail:
        if d["a"] == d["b"] == d["d"]:
            continue
        print()
        print("#%d %s" % (d["no"], d["q"]))
        print("   期望块: %s" % d["expect"])
        print("   名次: A=%s  B=%s  C=%s  D=%s" % (
            d["a"] or "x", d["b"] or "x", d["c"] or "x", d["d"] or "x"))
        print("   B 的 Top-3: %s" % " > ".join(d["top_b"]))
        print("   D 的 Top-3: %s" % " > ".join(d["top_d"]))

    print()
    print("跑完了。")


if __name__ == "__main__":
    main()
