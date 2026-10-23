# -*- coding: utf-8 -*-
"""题库体检：查重复题、keys 是否落在期望块里、题型分布异常。

check_questions.py 只验 expect 能不能对上块标题，
但验不了「这道题的问法是不是跟前面某题几乎一样」，
也验不了「我写进 keys 的词到底在不在那个块里」。

keys 写错的后果是判分永远错、而且不报错 —— 静默失败。
"""
import sys
import os
import difflib
import collections

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from question_bank import QUESTIONS  # noqa: E402
import knowledge  # noqa: E402

blocks = knowledge.load_blocks()
by_title = {}
for b in blocks:
    by_title.setdefault(b["title"], []).append(b)


def expects_of(item):
    e = item["expect"]
    return e if isinstance(e, list) else [e]


print("=" * 66)
print("题库体检   题目数：%d   知识块：%d" % (len(QUESTIONS), len(blocks)))
print("=" * 66)

# ---------------------------------------------------------------- 1. 近重复
print("\n[1] 近似重复题（相似度 >= 0.82）")
dups = []
for i in range(len(QUESTIONS)):
    for j in range(i + 1, len(QUESTIONS)):
        a, b = QUESTIONS[i]["q"], QUESTIONS[j]["q"]
        r = difflib.SequenceMatcher(None, a, b).ratio()
        if r >= 0.82:
            dups.append((r, i + 1, a, j + 1, b))
for r, i, a, j, b in sorted(dups, reverse=True)[:25]:
    print("  %.2f  #%-3d %s\n        #%-3d %s" % (r, i, a, j, b))
print("  共 %d 对" % len(dups))

# ---------------------------------------------------------------- 2. keys 落地
print("\n[2] keys 不在期望块里（写错就永远判错）")
bad = []
for idx, item in enumerate(QUESTIONS, 1):
    titles = expects_of(item)
    missing_title = [t for t in titles if t not in by_title]
    if missing_title:
        continue
    text = "\n".join(b["text"] for t in titles for b in by_title[t])
    low = text.lower()
    lost = [k for k in item.get("keys", []) if k.lower() not in low]
    if lost:
        bad.append((idx, item["q"], titles, lost))
for idx, q, titles, lost in bad:
    print("  #%-3d %s\n        期望=%s  缺失keys=%s" % (idx, q, "/".join(titles), lost))
print("  共 %d 题" % len(bad))

# ---------------------------------------------------------------- 3. 空 keys / 缺字段
print("\n[3] 字段异常")
prob = []
for idx, item in enumerate(QUESTIONS, 1):
    if not item.get("keys"):
        prob.append((idx, item["q"], "没有 keys"))
    if "level" not in item:
        prob.append((idx, item["q"], "没有 level"))
    if len(item["q"]) > 60:
        prob.append((idx, item["q"], "问句过长 %d 字" % len(item["q"])))
for p in prob:
    print("  #%-3d %-40s %s" % p)
print("  共 %d 条" % len(prob))

# ---------------------------------------------------------------- 4. 分布
print("\n[4] 各块出题数（看是否偏科）")
cnt = collections.Counter()
for item in QUESTIONS:
    for t in expects_of(item):
        cnt[t] += 1
for t, n in cnt.most_common():
    flag = "  <-- 偏多" if n >= 10 else ""
    print("  %-2d  %s%s" % (n, t, flag))
print("  最少 %d 题：%s" % (min(cnt.values()),
                          ", ".join(t for t, n in cnt.items() if n == min(cnt.values()))))
print("=" * 66)
