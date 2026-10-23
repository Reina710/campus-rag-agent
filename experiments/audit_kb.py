"""知识库体检：查题库自检查不到的问题。

跑法：
    python experiments/audit_kb.py

查四件事：
    1. 太短的块 —— 内容不够，答不了细节题
    2. 近似重复的块 —— 同一件事写在两处，检索会被劈成两半
    3. 一条事实落在多个块 —— 题库只标一个 expect，答对的块被判错
    4. 题库里的 keys 在非期望块同样全中 —— 说明这题的 expect 有歧义

第 3、4 条最要紧：它们不报错，只是悄悄把指标压低。
"""

import os
import re
import sys
from itertools import combinations

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import knowledge                      # noqa: E402
from question_bank import QUESTIONS   # noqa: E402

SHORT_BLOCK_CHARS = 90     # 低于这个字数，细节题基本答不出
DUP_RATIO = 0.55           # 块间相似度阈值（字面）


def norm(text):
    """去掉空白和标点，只留字，避免格式差异影响比较。"""
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text)


def bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def jaccard(a, b):
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / float(len(ba | bb))


def main():
    blocks = knowledge.load_blocks()
    print("=" * 70)
    print("知识库体检   块数：%d   总字数：%d" % (len(blocks), sum(len(b["text"]) for b in blocks)))
    print("=" * 70)

    # ---------- 1. 过短的块 ----------
    print("\n[1] 内容过短的块（< %d 字）" % SHORT_BLOCK_CHARS)
    short = sorted(blocks, key=lambda b: len(b["text"]))
    flagged = [b for b in short if len(b["text"]) < SHORT_BLOCK_CHARS]
    for b in flagged:
        print("  %4d 字  %s" % (len(b["text"]), b["title"]))
    if not flagged:
        print("  无")
    print("  中位数 %d 字，最短 %d 字，最长 %d 字"
          % (sorted(len(b["text"]) for b in blocks)[len(blocks) // 2],
             len(short[0]["text"]), len(short[-1]["text"])))

    # ---------- 2. 近似重复的块 ----------
    print("\n[2] 近似重复的块（字面相似度 >= %.2f）" % DUP_RATIO)
    pairs = []
    for a, b in combinations(blocks, 2):
        r = jaccard(norm(a["text"]), norm(b["text"]))
        if r >= DUP_RATIO:
            pairs.append((r, a["title"], b["title"]))
    for r, ta, tb in sorted(pairs, reverse=True):
        print("  %.2f  %s  <->  %s" % (r, ta, tb))
    if not pairs:
        print("  无")

    # ---------- 3. 同一组数字出现在多个块 ----------
    # 数字型事实（金额、时限、次数）最容易写重，重了就会互相干扰
    print("\n[3] 同一组数字出现在多个块（可能是一件事写了两遍）")
    num_owner = {}
    for b in blocks:
        for n in set(re.findall(r"\d+(?:\.\d+)?", b["text"])):
            if len(n) >= 2:                     # 跳过 1、2 这种编号
                num_owner.setdefault(n, set()).add(b["title"])
    shared = {n: ts for n, ts in num_owner.items() if len(ts) > 1}
    shown = 0
    for n, ts in sorted(shared.items(), key=lambda kv: -len(kv[1])):
        if len(ts) >= 3 or n in ("20", "48", "800", "30", "600"):
            print("  %-6s 出现在 %d 块：%s" % (n, len(ts), " / ".join(sorted(ts))[:90]))
            shown += 1
        if shown >= 12:
            break
    if not shown:
        print("  无")

    # ---------- 4. expect 有歧义的题 ----------
    # 如果一题的 keys 在另一个块里也全部命中，那题答对那块也算对，
    # 但题库只认 expect，指标会把它记成错。
    #
    # 前提：keys 得有区分度。像 "20"、"1" 这种本身在十几个块里都有，
    # 命中它们说明不了任何事，只能说明这个词太泛。
    print("\n[4] keys 在非期望块同样全中的题（expect 有歧义）")
    normal = [q for q in QUESTIONS if not isinstance(q["expect"], (list, tuple))
              and q["expect"] != "（无）"]

    # 每个 key 出现在多少个块里；超过 1/3 的块就算"泛词"，不算证据
    key_df = {}
    for b in blocks:
        t = norm(b["text"])
        for q in normal:
            for k in (q.get("keys") or []):
                kk = norm(str(k))
                if kk and kk in t:
                    key_df.setdefault(kk, set()).add(b["title"])
    max_df = max(2, len(blocks) // 3)

    amb, weak_keys = [], []
    for q in normal:
        keys = [str(k) for k in (q.get("keys") or [])]
        strong = [k for k in keys if len(key_df.get(norm(k), ())) <= max_df]
        if keys and not strong:
            weak_keys.append((q["q"], keys))
            continue
        if not strong:
            continue
        hit_blocks = []
        for b in blocks:
            t = norm(b["text"])
            if all(norm(k) in t for k in strong):
                hit_blocks.append(b["title"])
        others = [t for t in hit_blocks if t != q["expect"]
                  and t.split(" · ")[-1] != q["expect"]]
        if others:
            amb.append((q["q"], q["expect"], others, strong))

    for q, exp, others, strong in amb:
        print("  %s" % q[:38])
        print("      expect=%s   也全中：%s" % (exp, " / ".join(others)[:76]))
        print("      判分用的 key：%s" % "、".join(strong))
    print("  共 %d 题（%.1f%%）" % (len(amb), len(amb) * 100.0 / max(len(normal), 1)))

    print("\n[5] keys 全是泛词、几乎判不出对错的题")
    for q, keys in weak_keys:
        print("  %s   keys=%s" % (q[:36], "、".join(keys)))
    print("  共 %d 题" % len(weak_keys))


if __name__ == "__main__":
    main()
