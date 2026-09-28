# -*- coding: utf-8 -*-
"""
切块参数对照 —— 同一套题、同一个检索器，只变"怎么切块"，看命中率怎么变。

为什么要有这个对照？
    RAG 面试几乎必问"你怎么切块的、切多长最好"。
    这个脚本就是给这个问题的答案：不是拍脑袋说"500 字最好"，
    而是真跑一遍，看我的数据上切块参数到底怎么影响检索。

判据（关键，和 eval_retrieval 不同）：
    eval_retrieval 靠"标题匹配"判对错（expect 是块标题子串）。
    但 fixed 模式切出来的块没有标题（就叫"第 N 块"），标题匹配全失效。
    所以这里改用"内容匹配"：
        每道题的 keys 是"该出现的标志词"，只要这些词出现在
        捞回的块的【正文】里，就算命中。

    这样判据跟切块方式无关，纯看"正确内容有没有被捞回来"。

对照组（4 种切法）：
    heading          按标题切（当前基准，38 块）
    fixed/300        固定 300 字，重叠 50
    fixed/500        固定 500 字，重叠 50
    fixed/800        固定 800 字，重叠 50

只跑 freq（免费、快、最能暴露"切块"对字面匹配的影响）。
用法：
    python eval_chunk.py
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

TOP_K = 3


def _keys_of(item):
    """把 keys 拍平成纯字符串列表（去掉 AND 嵌套，只关心"出现没出现"）。"""
    flat = []
    for k in item["keys"]:
        if isinstance(k, list):
            flat.extend(k)
        else:
            flat.append(k)
    return flat


def judge(hits, keys):
    """捞回的块正文里，出现了几个标志词？"""
    text = "".join(h["block"]["text"] for h in hits)
    got = sum(1 for k in keys if k in text)
    return got, len(keys)


def run_mode(chunk_mode, size=None):
    """在某个切块参数下，跑一遍全部非陷阱题。"""
    config.CHUNK_MODE = chunk_mode
    if size is not None:
        config.CHUNK_SIZE = size
    config.CHUNK_OVERLAP = 50

    blocks = knowledge.load_blocks()
    n = 0
    hit = 0
    got_sum = 0
    key_sum = 0
    rows = []

    for it in QUESTIONS:
        if it["expect"] == "（无）":   # 陷阱题没有"正确答案"，跳过
            continue
        n += 1
        hits = R.freq_search(it["q"], blocks, TOP_K)
        got, total = judge(hits, _keys_of(it))
        if got > 0:
            hit += 1
        got_sum += got
        key_sum += total
        rows.append((it["q"], got, total, [h["block"]["title"] for h in hits]))

    label = chunk_mode if size is None else "%s/%d" % (chunk_mode, size)
    return {
        "label": label, "blocks": len(blocks), "n": n, "hit": hit,
        "recall": got_sum / key_sum if key_sum else 0.0, "rows": rows,
    }


def main():
    print("=" * 78)
    print("  切块参数对照（检索器 = freq，Top-K = %d）" % TOP_K)
    print("=" * 78)

    results = []
    configs = [
        ("heading", None),
        ("fixed", 300),
        ("fixed", 500),
        ("fixed", 800),
    ]
    for mode, size in configs:
        r = run_mode(mode, size)
        results.append(r)
        print("\n[%s]  %d 块" % (r["label"], r["blocks"]))
        print("  命中（至少一个标志词被捞回）：%d/%d = %.1f%%"
              % (r["hit"], r["n"], r["hit"] * 100.0 / r["n"]))
        print("  标志词召回率（捞回几个 / 总共几个）：%.1f%%"
              % (r["recall"] * 100.0))
        # 打印没命中的题，方便看是哪几道
        miss = [row for row in r["rows"] if row[1] == 0]
        if miss:
            print("  未命中 %d 道：" % len(miss))
            for q, got, total, titles in miss:
                print("    - %s（捞到：%s）" % (q[:24], " | ".join(titles)))

    # 汇总表
    print("\n" + "=" * 78)
    print("  汇总对比")
    print("=" * 78)
    print("  %-12s %-8s %-12s %-14s" % ("切块方式", "块数", "命中率", "标志词召回"))
    for r in results:
        print("  %-12s %-8d %6.1f%%      %6.1f%%"
              % (r["label"], r["blocks"], r["hit"] * 100.0 / r["n"],
                 r["recall"] * 100.0))
    print("=" * 78)

    # 落盘
    os.makedirs("reports", exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("reports", "chunk_%s.txt" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        f.write("切块参数对照 检索器=freq Top-K=%d\n" % TOP_K)
        f.write("%-12s %-8s %-12s %-14s\n" % ("切块方式", "块数", "命中率", "标志词召回"))
        for r in results:
            f.write("%-12s %-8d %6.1f%%      %6.1f%%\n"
                    % (r["label"], r["blocks"], r["hit"] * 100.0 / r["n"],
                       r["recall"] * 100.0))
        f.write("\n=== 未命中明细 ===\n")
        for r in results:
            miss = [row for row in r["rows"] if row[1] == 0]
            if miss:
                f.write("\n[%s] 未命中 %d 道\n" % (r["label"], len(miss)))
                for q, got, total, titles in miss:
                    f.write("  - %s（捞到：%s）\n" % (q, " | ".join(titles)))
    print("\n[报告已写入 %s]" % path)


if __name__ == "__main__":
    main()
