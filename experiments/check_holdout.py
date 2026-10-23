# -*- coding: utf-8 -*-
"""检查 holdout 切分是否可用：分布是否与 dev 一致、有没有把陷阱题切进去。"""
import sys
import os
import collections

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import question_bank as QB  # noqa: E402

allq = QB.QUESTIONS
dev = QB.dev_questions()
hold = QB.holdout_questions()


def dist(items):
    d = collections.Counter(it.get("difficulty", "normal") for it in items)
    lv = collections.Counter(it.get("level", "?") for it in items)
    return d, lv


print("总 %d  dev %d  holdout %d" % (len(allq), len(dev), len(hold)))
print()
for name, items in (("dev", dev), ("holdout", hold)):
    d, lv = dist(items)
    total = len(items)
    print("%-8s 共 %d 题" % (name, total))
    print("   题型：" + "  ".join("%s %d(%.0f%%)" % (k, v, v * 100.0 / total)
                                for k, v in sorted(d.items())))
    print("   层次：" + "  ".join("%s %d(%.0f%%)" % (k, v, v * 100.0 / total)
                                for k, v in sorted(lv.items())))
    print()

# 陷阱题在 holdout 里 = 永远跑不到检索评测
trap_in_hold = [it for it in hold if it["expect"] == "（无）"]
print("holdout 里的陷阱题：%d 道（这些在检索评测里会被排除，实际有效题数 %d）"
      % (len(trap_in_hold), len(hold) - len(trap_in_hold)))

# 每个主题段在 holdout 里的覆盖：看有没有整段掉出去
print("\n各块在 holdout 中的题数（0 = 该块完全没进 holdout）")
cnt_all = collections.Counter()
cnt_hold = collections.Counter()
for it in allq:
    for t in ([it["expect"]] if isinstance(it["expect"], str) else it["expect"]):
        cnt_all[t] += 1
for it in hold:
    for t in ([it["expect"]] if isinstance(it["expect"], str) else it["expect"]):
        cnt_hold[t] += 1
zero = [t for t in cnt_all if cnt_hold[t] == 0]
print("  完全没进 holdout 的块：%d / %d" % (len(zero), len(cnt_all)))
if zero:
    print("   " + ", ".join(zero))
