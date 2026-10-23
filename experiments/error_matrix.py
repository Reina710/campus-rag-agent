# -*- coding: utf-8 -*-
"""
四路检索器 · 错误矩阵
=====================

把 reports/retrieval_20260927_210932.txt 里 4 个检索器 × 27 题的逐题结果
对齐成一张矩阵，回答三个问题：

  1. 错题是随机分布的，还是集中在某一类题上？
  2. RRF 输的题，是不是「它两路输入里已经有一路错了」的那些？
  3. 哪些错是「方法问题」，哪些是「这一类题本身难」？

输出：
  - 逐题矩阵（四路排名 + 对错）
  - 对错模式分组（哪几道题是同一种错法）
  - 跨块题单独统计
  - RRF 与两路输入的交叉分析

运行：python error_matrix.py
只读报告，不联网、不改任何项目代码。
"""

import os
import re
import unicodedata


# ============================================================
# 显示工具
# ============================================================
def disp_width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def pad(text, width):
    return text + " " * max(0, width - disp_width(text))


def cut(text, width):
    """按显示宽度截断，中文算 2 列。"""
    out, w = "", 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width:
            break
        out += ch
        w += cw
    return out + " " * (width - w)


def hr(char="=", width=86):
    print(char * width)


# ============================================================
# 解析报告
# ============================================================
def report_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "reports", "retrieval_20260927_210932.txt")


def parse_report(path):
    """
    返回 {检索器名: {题号: {...}}}

    报告里每道题的原始格式：
        18. 学业奖学金分几等？...
            期望块=奖学金评选标准  捞回=1/1  排名=2
            捞到=奖学金 · 奖学金类型 | 奖学金 · 奖学金评选标准 | 竞赛 · 竞赛加分
    """
    text = open(path, encoding="utf-8", errors="replace").read()
    sections = re.split(r"^\[(\w+)\]\s*$", text, flags=re.M)
    data = {}

    for i in range(1, len(sections), 2):
        name = sections[i]
        body = sections[i + 1]
        chunks = re.split(r"^\s*(\d+)\.\s", body, flags=re.M)
        items = {}

        for j in range(1, len(chunks), 2):
            no = int(chunks[j])
            seg = chunks[j + 1]
            lines = [ln.strip() for ln in seg.splitlines() if ln.strip()]
            if not lines:
                continue
            question = lines[0]

            meta = re.search(
                r"期望块=(.*?)\s+捞回=(\d+)/(\d+)\s+排名=(\d+)", seg
            )
            if not meta:
                continue

            got_line = re.search(r"捞到=(.*)", seg)
            retrieved = []
            if got_line:
                retrieved = [x.strip() for x in got_line.group(1).split("|")]

            items[no] = {
                "question": question,
                "expect": meta.group(1).strip(),
                "got": int(meta.group(2)),
                "need": int(meta.group(3)),
                "rank": int(meta.group(4)),
                "retrieved": retrieved,
            }
        data[name] = items

    return data


# ============================================================
# 判定
# ============================================================
RETRIEVERS = ["freq", "bm25", "embed", "rrf"]


def mark(rank):
    """
    排名 1  -> "1 "   命中且排第一
    排名 >1 -> "2 "   命中但排在后面
    排名 0  -> "X "   前三里根本没找到
    """
    if rank == 0:
        return "X"
    return str(rank)


def is_top1(rank):
    return rank == 1


def is_hit(rank):
    return rank >= 1


# ============================================================
# 1. 逐题矩阵
# ============================================================
def print_matrix(data, numbers):
    hr()
    print("一、逐题矩阵（数字=排名，1 最好；X=前三名里没有）")
    hr()
    print(pad("题", 5) + pad("问题", 40) + pad("期望块", 22) + pad("freq", 6) + pad("bm25", 6) + pad("embed", 7) + "rrf")
    print("-" * 86)

    for no in numbers:
        base = data["freq"][no]
        row = pad(str(no), 5) + cut(base["question"], 40) + cut(base["expect"], 22)
        for name in RETRIEVERS:
            row += pad(mark(data[name][no]["rank"]), 6 if name != "embed" else 7)
        print(row)


# ============================================================
# 2. 对错模式分组
# ============================================================
def pattern_of(item_by_retriever, no):
    """把四路是否 Top-1 命中压成一个字符串，比如 'XXVX' 里 V 表示对。"""
    s = ""
    for name in RETRIEVERS:
        s += "V" if is_top1(item_by_retriever[name][no]["rank"]) else "X"
    return s


def print_pattern_groups(data, numbers):
    hr()
    print("二、对错模式分组（V=Top-1 命中，X=没中）")
    hr()
    print("顺序：freq / bm25 / embed / rrf")
    print()

    groups = {}
    for no in numbers:
        p = pattern_of(data, no)
        groups.setdefault(p, []).append(no)

    for p in sorted(groups, key=lambda x: (-len(groups[x]), x)):
        nos = groups[p]
        desc = describe_pattern(p)
        print(pad(p, 10) + pad(f"{len(nos)} 题", 8) + pad(desc, 34) + "题号: " + ", ".join(str(n) for n in nos))


def describe_pattern(p):
    """把 'VVXX' 翻译成人话。"""
    labels = ["字频", "bm25", "向量", "RRF"]
    ok = [labels[i] for i, c in enumerate(p) if c == "V"]
    bad = [labels[i] for i, c in enumerate(p) if c == "X"]
    if not ok:
        return "四路全错"
    if not bad:
        return "四路全对"
    if len(ok) == 1:
        return "只有" + ok[0] + "对"
    return "对：" + "+".join(ok) + "；错：" + "+".join(bad)


# ============================================================
# 3. 跨块题统计
# ============================================================
def print_cross_block(data, numbers):
    hr()
    print("三、跨块题（答案需要不止一个块）")
    hr()

    cross = [no for no in numbers if data["freq"][no]["need"] > 1]
    if not cross:
        print("报告里没有 need>1 的题。")
        return

    print("题号  需要块数  " + pad("freq", 8) + pad("bm25", 8) + pad("embed", 8) + "rrf")
    print("-" * 60)
    for no in cross:
        need = data["freq"][no]["need"]
        row = pad(str(no), 6) + pad(str(need), 10)
        for name in RETRIEVERS:
            it = data[name][no]
            row += pad(f"{it['got']}/{it['need']}", 8)
        print(row)

    print()
    print("  说明：这里的数字是「捞回了几个 / 应该捞回几个」。")
    print("        它和排名不是一回事 —— 排名=1 但捞回 1/2，意思是")
    print("        「找对了一个块，但另一个块根本没进前三」，")
    print("        这种情况 Top-1 命中率看不出来，只有 recall 能暴露。")


# ============================================================
# 4. RRF 与两路输入的交叉分析
# ============================================================
def print_rrf_cross(data, numbers):
    hr()
    print("四、RRF 和它两路输入的关系")
    hr()

    rrf_wrong_input_right = []
    rrf_right_input_wrong = []
    rrf_wrong_all_wrong = []

    for no in numbers:
        rrf_ok = is_top1(data["rrf"][no]["rank"])
        bm25_ok = is_top1(data["bm25"][no]["rank"])
        embed_ok = is_top1(data["embed"][no]["rank"])
        any_input_ok = bm25_ok or embed_ok

        if (not rrf_ok) and any_input_ok:
            rrf_wrong_input_right.append((no, bm25_ok, embed_ok))
        elif rrf_ok and not (bm25_ok and embed_ok):
            rrf_right_input_wrong.append((no, bm25_ok, embed_ok))
        elif (not rrf_ok) and (not any_input_ok):
            rrf_wrong_all_wrong.append(no)

    print("① RRF 错了，但它的某一路输入是对的 —— 融合把正确答案弄丢了：")
    if rrf_wrong_input_right:
        for no, bm25_ok, embed_ok in rrf_wrong_input_right:
            who = "bm25" if bm25_ok and not embed_ok else ("embed" if embed_ok and not bm25_ok else "两路都对")
            print(f"     第 {no:>2} 题   对的那一路：{who}")
    else:
        print("     无")
    print()

    print("② RRF 对了，但它的输入没有一路全对 —— 融合把答案救回来了：")
    if rrf_right_input_wrong:
        for no, bm25_ok, embed_ok in rrf_right_input_wrong:
            print(f"     第 {no:>2} 题   bm25 {'对' if bm25_ok else '错'} / embed {'对' if embed_ok else '错'}")
    else:
        print("     无")
    print()

    print(f"③ RRF 错了，两路输入也都错 —— 融合再强也救不了：{len(rrf_wrong_all_wrong)} 题")
    if rrf_wrong_all_wrong:
        print("     题号: " + ", ".join(str(n) for n in rrf_wrong_all_wrong))


# ============================================================
# 5. 自动结论
# ============================================================
def print_conclusions(data, numbers):
    hr()
    print("五、从这张矩阵里能直接读出的结论")
    hr()

    total = len(numbers)

    # 每路 Top-1 错题数
    print("每路的 Top-1 错题数：")
    for name in RETRIEVERS:
        wrong = [no for no in numbers if not is_top1(data[name][no]["rank"])]
        print(f"  {pad(name, 7)}{len(wrong):>2} / {total}   题号: " + (", ".join(str(n) for n in wrong) or "无"))
    print()

    # 四路对错完全一致的题
    same_all = [no for no in numbers if len({is_top1(data[n][no]['rank']) for n in RETRIEVERS}) == 1]
    print(f"四路结果完全一致的题：{len(same_all)} / {total}")
    if same_all:
        print("  这些题上换检索器没有任何意义，题号: " + ", ".join(str(n) for n in same_all))
    print()

    # 只有向量对的题
    only_embed = [no for no in numbers if pattern_of(data, no) == "XXVX"]
    print(f"「只有向量对」的题：{len(only_embed)} 道" + (f"，题号: {', '.join(map(str, only_embed))}" if only_embed else ""))
    print()

    # 向量对但 RRF 错
    embed_ok_rrf_bad = [no for no in numbers if is_top1(data['embed'][no]['rank']) and not is_top1(data['rrf'][no]['rank'])]
    print(f"向量对、RRF 却错的题：{len(embed_ok_rrf_bad)} 道" + (f"，题号: {', '.join(map(str, embed_ok_rrf_bad))}" if embed_ok_rrf_bad else ""))
    print("  → 这几道就是「融合带来的净损失」，也是那 3.7 个点的全部来源。")
    print()

    rrf_better = [no for no in numbers if is_top1(data['rrf'][no]['rank']) and not is_top1(data['embed'][no]['rank'])]
    print(f"RRF 对、向量错的题：{len(rrf_better)} 道" + (f"，题号: {', '.join(map(str, rrf_better))}" if rrf_better else ""))
    print("  → 这是「融合带来的净收益」。")
    print()
    print("  净收益 - 净损失 = 你看到的 92.6% vs 96.3%。")


def main():
    path = report_path()
    if not os.path.exists(path):
        print("找不到报告：" + path)
        return

    data = parse_report(path)
    missing = [n for n in RETRIEVERS if n not in data]
    if missing:
        print("报告里缺少这些检索器：" + ", ".join(missing))
        return

    numbers = sorted(set(data["freq"].keys()) & set(data["bm25"].keys())
                     & set(data["embed"].keys()) & set(data["rrf"].keys()))

    hr()
    print("四路检索器 · 错误矩阵")
    print(f"报告：{os.path.basename(path)}    题数：{len(numbers)}    Top-K = 3")
    hr()

    print_matrix(data, numbers)
    print_pattern_groups(data, numbers)
    print_cross_block(data, numbers)
    print_rrf_cross(data, numbers)
    print_conclusions(data, numbers)


if __name__ == "__main__":
    main()
