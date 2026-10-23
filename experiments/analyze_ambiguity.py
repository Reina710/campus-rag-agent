"""量化"检索错了"和"判分太严"各占多少。

背景：知识库重构成「综述块 + 细节块」两层（如 论文流程 与 开题报告/中期考核/论文答辩），
同一句话（"第二学期末"）会同时出现在综述块和细节块里。
题库每题只标一个 expect，于是检索到另一个"同样能答"的块，会被判成错。

这个脚本对同一批题算两种口径：
    严格：Top-1 必须是 expect 指定的块（现用口径）
    宽松：Top-1 只要包含判分 key 全中（也就是真能答出来）就算对
两个数的差 = 判分口径造成的虚低。

跑法：
    python experiments/analyze_ambiguity.py            # dev 集
    python experiments/analyze_ambiguity.py --holdout
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import knowledge                                    # noqa: E402
import retriever as R                               # noqa: E402
from question_bank import dev_questions, holdout_questions   # noqa: E402


def norm(s):
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", s)


def main():
    split = "holdout" if "--holdout" in sys.argv else "dev"
    items = holdout_questions() if split == "holdout" else dev_questions()
    items = [q for q in items if q["expect"] != "（无）"]
    blocks = knowledge.load_blocks()

    # 泛词阈值：一个 key 出现在超过 1/3 的块里，就不算证据
    key_df = {}
    for b in blocks:
        t = norm(b["text"])
        for q in items:
            for k in (q.get("keys") or []):
                kk = norm(str(k))
                if kk and kk in t:
                    key_df.setdefault(kk, set()).add(b["title"])
    max_df = max(2, len(blocks) // 3)

    def strong_keys(q):
        return [str(k) for k in (q.get("keys") or [])
                if len(key_df.get(norm(str(k)), ())) <= max_df]

    def strict(hits, q):
        ex = [q["expect"]] if isinstance(q["expect"], str) else list(q["expect"])
        for i, h in enumerate(hits, 1):
            if any(e in h["block"]["title"] for e in ex):
                return i
        return None

    def answers(block, q):
        """这一块的内容是否真能回答问题（判分 key 全中）。"""
        keys = strong_keys(q)
        if not keys:
            return False
        t = norm(block["text"])
        return all(norm(k) in t for k in keys)

    s1 = l1 = 0
    s3 = l3 = 0
    rescued = []          # 严格 Top-1 错、但排第一的块其实答得出来
    for q in items:
        hits = R.bm25_search(q["q"], blocks, 3)
        ps = strict(hits, q)
        ok1_l = (ps == 1) or (bool(hits) and answers(hits[0]["block"], q))
        ok3_l = (ps is not None) or any(answers(h["block"], q) for h in hits)

        s1 += ps == 1
        l1 += ok1_l
        s3 += ps is not None
        l3 += ok3_l
        if ps != 1 and hits and answers(hits[0]["block"], q):
            rescued.append((q, hits[0]["block"]["title"]))

    n = len(items)
    print("=" * 74)
    print("判分口径差异（%s 集，%d 题，BM25 单路）" % (split, n))
    print("=" * 74)
    print("            严格口径（必须是 expect 块）   宽松口径（能答就算对）")
    print("Top-1        %6.1f%%                        %6.1f%%" % (s1 * 100.0 / n, l1 * 100.0 / n))
    print("Top-3        %6.1f%%                        %6.1f%%" % (s3 * 100.0 / n, l3 * 100.0 / n))
    print()
    print("严格错、宽松对 %d 题（占 %.1f%%）—— 这部分的块内容是对的，只是不是标的那一块"
          % (len(rescued), len(rescued) * 100.0 / n))
    print()
    print("明细（题 / 标答 / 实际排第一）")
    for q, t in rescued:
        print("  %-34s 标答=%-12s 实际=%s" % (q["q"][:32], q["expect"], t))

    # ---------- 附带：错的时候错到哪去了 ----------
    print()
    print("=" * 74)
    print("Top-1 错因分布（期望块 → 实际排第一的块，只列前 10）")
    print("=" * 74)
    conf = {}
    for q in items:
        if isinstance(q["expect"], (list, tuple)):
            continue
        hits = R.bm25_search(q["q"], blocks, 1)
        if not hits:
            continue
        got = hits[0]["block"]["title"]
        if q["expect"] not in got:
            conf[(q["expect"], got)] = conf.get((q["expect"], got), 0) + 1
    for (exp, got), c in sorted(conf.items(), key=lambda kv: -kv[1])[:10]:
        print("  %2d 次   %-14s -> %s" % (c, exp, got))
    if not conf:
        print("  无")

    # ---------- 附带：细节题为什么 BM25 反而输给字频 ----------
    print()
    print("=" * 74)
    print("L3 细节题：字频对、BM25 错的题（看数字是怎么被分词的）")
    print("=" * 74)
    shown = 0
    for q in items:
        if q.get("level") != "L3" or q["expect"] == "（无）":
            continue
        f = R.freq_search(q["q"], blocks, 1)
        b = R.bm25_search(q["q"], blocks, 1)
        if f and b and strict(f, q) == 1 and strict(b, q) != 1:
            print("  %-32s 标答=%-14s BM25给了=%s"
                  % (q["q"][:30], q["expect"], b[0]["block"]["title"]))
            shown += 1
            if shown >= 8:
                break
    if not shown:
        print("  无")


if __name__ == "__main__":
    main()
