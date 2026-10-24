# -*- coding: utf-8 -*-
"""
把用户点踩的反馈聚合成 bad case 池。

为什么需要：
    反馈落在 data/feedback.jsonl（一行一条），散着看没有信息量。
    这个脚本按题聚合、按点踩次数排序，输出 data/bad_cases.md，
    你一眼就能看到"哪些题被踩最多"。

关键原则：**点踩不等于答错。**
    用户可能手滑、可能只是不喜欢语气、也可能问题本身就模糊。
    所以脚本只负责"把候选聚起来"，判断"是不是真错了"仍然要人看一眼。
    对不在题库里的题，脚本会给出可直接粘进 question_bank.py 的骨架，
    但 expect（正确答案块）需要你填——这个没法自动猜。

用法：
    python experiments/harvest_feedback.py
    python experiments/harvest_feedback.py --min-count 2   # 只看被踩 >= 2 次的题
    python experiments/harvest_feedback.py --all          # 好评也统计（默认只统计差评）

输出：
    data/bad_cases.md   人看的报告
    终端                 摘要
"""

import datetime
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config
import question_bank


FEEDBACK = os.path.join(config.PROJECT_ROOT, "data", "feedback.jsonl")
OUT = os.path.join(config.PROJECT_ROOT, "data", "bad_cases.md")


def _norm(q):
    """分组用：去掉空白和常见标点，避免同一句问法被拆成两条。"""
    q = (q or "").strip()
    q = re.sub(r"[\s，。？！?！、；;：:]+", "", q)
    return q


def load_feedback():
    if not os.path.exists(FEEDBACK):
        return []
    rows = []
    with open(FEEDBACK, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def main():
    min_count = 1
    include_up = False
    for i, arg in enumerate(sys.argv):
        if arg == "--min-count" and i + 1 < len(sys.argv):
            min_count = int(sys.argv[i + 1])
        if arg == "--all":
            include_up = True

    rows = load_feedback()
    if not rows:
        print("没有反馈数据（%s 不存在或为空）。" % FEEDBACK)
        print("先去页面上点几次 👍/👎，再回来跑这个脚本。")
        return

    downs = [r for r in rows if r.get("rating") == "down"]
    ups = [r for r in rows if r.get("rating") == "up"]

    # 按题聚合
    groups = defaultdict(lambda: {"count": 0, "answers": [], "last": ""})
    for r in downs:
        q = (r.get("question") or "").strip()
        if not q:
            continue
        g = groups[_norm(q)]
        g["question"] = q
        g["count"] += 1
        if r.get("answer"):
            g["answers"].append(r["answer"])
        g["last"] = max(g["last"], r.get("ts") or "")

    bank = {_norm(q["q"]) for q in question_bank.QUESTIONS}

    items = sorted(groups.items(), key=lambda kv: -kv[1]["count"])
    items = [(k, v) for k, v in items if v["count"] >= min_count]

    total = len(rows)
    down_n = len(downs)
    in_bank = sum(1 for k, _ in items if k in bank)
    not_in_bank = len(items) - in_bank

    print("=" * 60)
    print("反馈总数 %d | 点踩 %d (%.1f%%) | 点踩题 %d 道"
          % (total, down_n, down_n * 100.0 / total if total else 0, len(groups)))
    print("其中已在题库：%d，不在题库（建议补）：%d" % (in_bank, not_in_bank))
    print("=" * 60)
    for k, v in items:
        flag = "已在题库" if k in bank else "建议补"
        print("[%s] %s  （被踩 %d 次）" % (flag, v["question"], v["count"]))
    print()

    # 落盘
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Bad Case 池\n\n")
        f.write("> 由 `experiments/harvest_feedback.py` 从 `data/feedback.jsonl` 生成。\n")
        f.write("> 生成时间：%s\n\n" % now)

        f.write("## 概览\n\n")
        f.write("| 指标 | 值 |\n|---|---|\n")
        f.write("| 反馈总数 | %d |\n" % total)
        f.write("| 点踩 | %d（%.1f%%） |\n" % (down_n, down_n * 100.0 / total if total else 0))
        if include_up:
            f.write("| 好评 | %d |\n" % len(ups))
        f.write("| 点踩涉及的题 | %d |\n" % len(groups))
        f.write("| 已在题库 | %d |\n" % in_bank)
        f.write("| 不在题库（建议补） | %d |\n\n" % not_in_bank)

        f.write("## 点踩最多的题\n\n")
        if not items:
            f.write("（没有达到阈值的点踩题）\n")
        for i, (k, v) in enumerate(items, 1):
            status = "已在题库" if k in bank else "**不在题库 → 建议补**"
            f.write("### %d. %s（被踩 %d 次）\n\n" % (i, v["question"], v["count"]))
            f.write("- 题库状态：%s\n" % status)
            if v["last"]:
                f.write("- 最近时间：%s\n" % v["last"])
            if v["answers"]:
                f.write("- 最近一次回答：\n\n  > %s\n\n"
                        % v["answers"][-1].replace("\n", "\n  > "))
            f.write("\n")

        # 给不在题库的题生成可直接粘贴的骨架
        pending = [(k, v) for k, v in items if k not in bank]
        if pending:
            f.write("## 建议补充的题（骨架，expect 需人工填）\n\n")
            f.write("```python\n")
            for k, v in pending:
                f.write('    {"q": "%s",\n' % v["question"].replace('"', '\\"'))
                f.write('     "expect": "（待填：正确答案所在的块标题）",\n')
                f.write('     "keys": ["（待填：答案里应出现的关键词）"],\n')
                f.write('     "level": "L2"},\n\n')
            f.write("```\n")

    print("[报告已写入 %s]" % OUT)


if __name__ == "__main__":
    main()
