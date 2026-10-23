# -*- coding: utf-8 -*-
"""
五方案对照评测：一次算完全量排名，五个方案共用，避免重复调 API。

为什么要"共用排名"：
    如果每个方案各自跑一遍，214 题 × 5 套 = 1070 次查询，
    其中 embed 每次都要联网，光等待就足够泡杯茶。
    而 bm25/freq 的排名和方案无关，embed 排名也只跟问题有关，
    所以算一次、切片复用，214 题只要 214 次 embed + 214 次 rerank。

对比的五组：
    freq    字频（逐字匹配，不花钱）
    bm25    分词 + IDF（不花钱）
    embed   向量单路（现状最强基线）
    rrf     全量 RRF（旧融合方案）
    hybrid  宽召回 -> RRF 合并 -> rerank 精排（新流水线）

指标：
    Top-1     正确块排第一的比例
    Top-3     前 3 条里有正确块
    recall@3  该找的块覆盖了几个（跨块题才有区分度）
    MRR       平均倒数排名

另外按 level（L1 直问 / L2 改写 / L3 细节 / L4 口语）和 difficulty
分层统计 —— 平均值会掩盖"某一类题整体崩了"。

用法：
    python src/eval_pipeline.py                 # 全量 214 题
    python src/eval_pipeline.py --split dev     # 只跑调参集（149 题）
    python src/eval_pipeline.py --split holdout # 只跑独立评测集（65 题，不参与调参）
    python src/eval_pipeline.py --limit 10      # 先跑 10 题试水
    python src/eval_pipeline.py --no-rerank     # 跳过 rerank（省钱/看降级）
    python src/eval_pipeline.py --offline       # 只跑 freq/bm25（没额度也能做回归）

向量服务不可用时会自动降级成 offline，不会整体崩 —— 这条和线上同一个原则。
但降级后算不出 RRF 和 hybrid（它们必须有第二路），报告会明确标注缺哪几组。
"""

import os
import sys
import time
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import knowledge
import retriever as R
import rerank as RR
import question_bank as QB
from question_bank import QUESTIONS

FINAL_K = config.TOP_K
RECALL_K = config.RECALL_K
RERANK_N = config.RERANK_N


# ------------------------------------------------------------
# 指标
# ------------------------------------------------------------
def expects_of(item):
    e = item["expect"]
    return [e] if isinstance(e, str) else list(e)


def hit_pos(hits, item):
    """正确块在 hits 里最靠前的名次（1 起）；不在返回 None。"""
    ex = expects_of(item)
    for i, h in enumerate(hits, 1):
        t = h["block"]["title"]
        if any(e in t for e in ex):
            return i
    return None


def recall_at(hits, item, k):
    """前 k 条覆盖了几个应找的块（跨块题 0/1 或 0.5/1）。"""
    ex = expects_of(item)
    got = set()
    for h in hits[:k]:
        t = h["block"]["title"]
        for e in ex:
            if e in t:
                got.add(e)
    return len(got) / len(ex)


def metrics(rows):
    """rows: [(item, hits)]，陷阱题已在外面排除。"""
    n = len(rows)
    top1 = top3 = 0
    mrr = rc = 0.0
    for item, hits in rows:
        p = hit_pos(hits, item)
        if p == 1:
            top1 += 1
        if p is not None and p <= FINAL_K:
            top3 += 1
            mrr += 1.0 / p
        rc += recall_at(hits, item, FINAL_K)
    return {"n": n, "top1": top1 / n, "top3": top3 / n,
            "recall": rc / n, "mrr": mrr / n}


# ------------------------------------------------------------
# 一次算出三路全量排名
# ------------------------------------------------------------
def full_rankings(question, blocks, allow_embed=True):
    fq = R.freq_search(question, blocks, len(blocks))
    bm = R.bm25_search(question, blocks, len(blocks))
    em = R.embed_search(question, blocks, len(blocks)) if allow_embed else None
    return fq, bm, em


def embed_available():
    """探测一次向量服务。失败说明欠费/断网/欠额度，整轮改跑离线。"""
    try:
        R.embed("连通性探测")
        return True, ""
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:90])


def hybrid_from(bm, em, blocks, question, use_rerank=True):
    """在已算好的两路全量排名上跑宽召回 -> RRF -> rerank。"""
    pool_ids = {h["block"]["id"] for h in bm[:RECALL_K]}
    pool_ids |= {h["block"]["id"] for h in em[:RECALL_K]}
    pool = [b for b in blocks if b["id"] in pool_ids]

    merged = R._rrf_merge(bm, em, pool)[:RERANK_N]

    if use_rerank and config.RERANK_ENABLED:
        reranked = RR.rerank(question, merged)
        if reranked:
            return reranked[:FINAL_K], True
        return merged[:FINAL_K], False      # 降级
    return merged[:FINAL_K], False


# ------------------------------------------------------------
# 报告
# ------------------------------------------------------------
def layer_table(rows, label):
    """按 level 分层看 Top-1 / recall@3。"""
    by = defaultdict(list)
    for item, hits in rows:
        by[item.get("level", "?")].append((item, hits))
    out = []
    for lv in ("L1", "L2", "L3", "L4"):
        if lv not in by:
            continue
        m = metrics(by[lv])
        out.append("    %-4s %2d题  Top-1 %5.1f%%  Top-3 %5.1f%%  recall@%d %.3f"
                   % (lv, m["n"], m["top1"] * 100, m["top3"] * 100, FINAL_K, m["recall"]))
    return out


def main():
    limit = None
    use_rerank = "--no-rerank" not in sys.argv
    offline = "--offline" in sys.argv
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    blocks = knowledge.load_blocks()

    degrade_reason = ""
    if not offline:
        ok, why = embed_available()
        if not ok:
            offline = True
            degrade_reason = why
            use_rerank = False

    items = [q for i, q in enumerate(QUESTIONS, 1) if not q["expect"] == "（无）"]

    # 数据集切分：默认全量；--dev 只看调参集；--holdout 只看独立集
    split = "all"
    if "--holdout" in sys.argv:
        split = "holdout"
    elif "--dev" in sys.argv:
        split = "dev"
    elif "--split" in sys.argv:
        _si = sys.argv.index("--split")
        if _si + 1 < len(sys.argv) and sys.argv[_si + 1] in ("dev", "holdout", "all"):
            split = sys.argv[_si + 1]
    if split != "all":
        keep = {q["q"] for q in (QB.holdout_questions() if split == "holdout"
                                 else QB.dev_questions())}
        items = [q for q in items if q["q"] in keep]

    for i, it in enumerate(items, 1):
        it["_no"] = i                     # 报告里用的题号（已排除陷阱题）
    if limit:
        items = items[:limit]

    print("=" * 84)
    print("五方案对照评测" + ("   [离线降级模式]" if offline else ""))
    print("=" * 84)
    print("知识库 %d 块 | 参与检索的题 %d 道（陷阱题另算，不考检索）"
          % (len(blocks), len(items)))
    if split != "all":
        print("数据集：%s（%s）" % (
            "holdout 独立集" if split == "holdout" else "dev 调参集",
            "未参与调参，用于最终报告" if split == "holdout" else "调参时看这套"))
    if offline:
        print("向量服务不可用：%s" % (degrade_reason or "手动 --offline"))
        print("本次只跑 字频 / BM25 两组，RRF 与 hybrid 需要第二路，无法计算")
    else:
        print("宽召回 RECALL_K=%d | 送 rerank %d 条 | 最终取 %d 条 | rerank=%s"
              % (RECALL_K, RERANK_N, FINAL_K, "开" if use_rerank else "关"))
    print()

    all_keys = ("freq", "bm25", "embed", "rrf", "hybrid")
    active = ("freq", "bm25") if offline else all_keys
    results = {k: {"rows": [], "others": {}} for k in all_keys}
    rows_bm, rows_em = [], []             # 给 diff 用
    rerank_ok = rerank_fail = 0
    t_all = time.time()
    t_rerank = 0.0

    for idx, item in enumerate(items, 1):
        q = item["q"]
        fq, bm, em = full_rankings(q, blocks, allow_embed=not offline)

        rrf_full = hy = None
        ok = False
        if em is not None:
            # 全量 RRF：在全部 38 块上算 RRF 分，取前 3
            rrf_full = R._rrf_merge(bm, em, blocks)[:FINAL_K]

            t0 = time.time()
            hy, ok = hybrid_from(bm, em, blocks, q, use_rerank)
            t_rerank += time.time() - t0
            if use_rerank:
                rerank_ok += 1 if ok else 0
                rerank_fail += 0 if ok else 1

        results["freq"]["rows"].append((item, fq[:FINAL_K]))
        results["bm25"]["rows"].append((item, bm[:FINAL_K]))
        if em is not None:
            results["embed"]["rows"].append((item, em[:FINAL_K]))
            results["rrf"]["rows"].append((item, rrf_full))
            results["hybrid"]["rows"].append((item, hy))
        rows_bm.append((item, bm[:FINAL_K]))
        rows_em.append((item, em[:FINAL_K]) if em is not None else (item, []))

        if idx % 10 == 0 or idx == len(items):
            print("  进度 %3d/%3d   已用 %.1fs" % (idx, len(items), time.time() - t_all))

    used = time.time() - t_all

    # ---------------- 汇总 ----------------
    names = [("freq", "① 字频"), ("bm25", "② BM25"),
             ("embed", "③ 向量单路"), ("rrf", "④ 全量 RRF"),
             ("hybrid", "⑤ 宽召回+RRF+rerank")]
    names = [(k, l) for k, l in names if k in active]
    summary = []
    print()
    print("=" * 84)
    print("总表（%d 道有标准答案的题）" % len(items))
    print("=" * 84)
    print("%-22s %8s %8s %10s %8s" % ("方案", "Top-1", "Top-3", "recall@3", "MRR"))
    print("-" * 84)
    for key, label in names:
        m = metrics(results[key]["rows"])
        line = "%-22s %7.1f%% %7.1f%% %10.3f %8.3f" % (
            label, m["top1"] * 100, m["top3"] * 100, m["recall"], m["mrr"])
        print(line)
        summary.append(line)

    # ---------------- 分层 ----------------
    print()
    print("=" * 84)
    print("按问法分层（L1 直问 / L2 改写 / L3 细节 / L4 口语）")
    print("=" * 84)
    layer_lines = []
    for key, label in names:
        print("%s" % label)
        ls = layer_table(results[key]["rows"], label)
        for l in ls:
            print(l)
        layer_lines.append(label)
        layer_lines.extend(ls)

    # ---------------- 易混淆题 ----------------
    print()
    print("=" * 84)
    print("易混淆题（两块高度相似，专考'排第一的对不对'）")
    print("=" * 84)
    pairs = [it for it in items if it.get("difficulty") == "cross_pair"]
    pair_lines = []
    if pairs:
        print("%-22s %8s %8s" % ("方案", "Top-1", "recall@3"))
        for key, label in names:
            sub = [(it, h) for it, h in results[key]["rows"]
                   if it.get("difficulty") == "cross_pair"]
            m = metrics(sub)
            line = "%-22s %7.1f%% %8.3f" % (label, m["top1"] * 100, m["recall"])
            print(line)
            pair_lines.append(line)
    else:
        print("（本次没有易混淆题）")

    # ---------------- 按题型分层 ----------------
    print()
    print("=" * 84)
    print("按题型分层（Top-1 命中 / 题数）")
    print("=" * 84)
    kinds = (("normal", "常规"), ("cross", "跨块"),
             ("cross_pair", "易混淆"), ("inject", "注入"))
    print("%-22s %s" % ("方案", "".join("%14s" % n for _, n in kinds)))
    type_lines = []
    for key, label in names:
        cells = []
        for d, _ in kinds:
            sub = [(it, h) for it, h in results[key]["rows"]
                   if it.get("difficulty", "normal") == d]
            if sub:
                m = metrics(sub)
                cells.append("%6.1f%%(%2d题)" % (m["top1"] * 100, m["n"]))
            else:
                cells.append("%14s" % "-")
        line = "%-22s %s" % (label, "".join("%14s" % c for c in cells))
        print(line)
        type_lines.append(line)

    # ---------------- rerank 到底干了什么 ----------------
    if rerank_ok or rerank_fail:
        print()
        print("=" * 84)
        print("rerank 的实际作用")
        print("=" * 84)
        print("  调用成功 %d 题，失败/降级 %d 题" % (rerank_ok, rerank_fail))
        print("  rerank 总耗时 %.1fs（平均每题 %.2fs）" % (t_rerank, t_rerank / max(len(items), 1)))

    # 两路 Top-1 分歧题（线上 cascade 只在这些人身上调 rerank）
    disagree = []
    for (it, bh), (_, eh), (_, hh) in zip(results["bm25"]["rows"],
                                          results["embed"]["rows"],
                                          results["hybrid"]["rows"]):
        t_b = bh[0]["block"]["title"] if bh else ""
        t_e = eh[0]["block"]["title"] if eh else ""
        if t_b != t_e:
            disagree.append((it, hh))
    if disagree:
        m = metrics(disagree)
        print("  两路 Top-1 分歧 %d 题（%.0f%%），其中 hybrid Top-1 %.1f%%、recall@3 %.3f"
              % (len(disagree), len(disagree) * 100.0 / len(items),
                 m["top1"] * 100, m["recall"]))

    # ---------------- 逐题差异 ----------------
    if "hybrid" in active:
        print()
        print("=" * 84)
        print("hybrid 与各方案结果不同的题（只看名次变化）")
        print("=" * 84)
    diff_lines = []
    vs = [("BM25", "bm25"), ("向量单路", "embed"), ("全量RRF", "rrf")]
    for name, key in [(n, k) for n, k in vs if k in active and "hybrid" in active]:
        print("\n[vs %s]" % name)
        d = []
        for (item, hh), (_, ho) in zip(results["hybrid"]["rows"], results[key]["rows"]):
            ph, po = hit_pos(hh, item), hit_pos(ho, item)
            if ph == po:
                continue
            d.append("  #%-3d %-30s hybrid=%-4s %-8s=%-4s  期望=%s"
                     % (item["_no"], item["q"][:28], ph or "x",
                        name, po or "x", "/".join(expects_of(item))))
        print("\n".join(d) if d else "  （无差异）")
        diff_lines.append("\n[vs %s]\n" % name + ("\n".join(d) if d else "  （无差异）"))

    # ---------------- 落盘 ----------------
    os.makedirs("reports", exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("reports", "pipeline_%s_%s.txt" % (split, stamp))
    with open(path, "w", encoding="utf-8") as f:
        f.write("%s  %d 题  RECALL_K=%d RERANK_N=%d FINAL_K=%d  数据集=%s\n"
                % ("离线降级（只有 freq/bm25）" if offline else "五方案对照",
                   len(items), RECALL_K, RERANK_N, FINAL_K, split))
        if offline:
            f.write("向量服务不可用：%s\n" % (degrade_reason or "手动 --offline"))
        f.write("总耗时 %.1fs  rerank成功 %d 降级 %d\n\n"
                % (used, rerank_ok, rerank_fail))
        f.write("\n".join(summary))
        f.write("\n\n=== 分层 ===\n" + "\n".join(layer_lines))
        f.write("\n\n=== 按题型 ===\n" + "\n".join(type_lines))
        f.write("\n\n=== 易混淆 ===\n" + "\n".join(pair_lines))
        f.write("\n\n=== 逐题差异 ===\n" + "\n".join(diff_lines))

    print()
    print("[报告已写入 %s]" % path)
    print("总耗时 %.1fs" % used)


if __name__ == "__main__":
    main()
