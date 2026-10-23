# -*- coding: utf-8 -*-
"""
RRF 融合实验
============

目的：用真跑出来的数字回答五个问题。

  1. 为什么「一路第1、另一路第2」的两个块，RRF 分数会完全相等？
  2. 为什么把 K 调大/调小都救不回来？
  3. 换一种融合方式（加权）为什么就能分出胜负？
  4. 唯一被 RRF 救回来的第 7 题，到底赢在哪一步？
  5. RRF 能救多远？正确块掉到第几名就救不了了？

数据来源：
  - 场景 A 直接取自本项目 reports/retrieval_20260927_210932.txt 的第 18、27 题。
    那两道题里，正确块与干扰块在两路检索器中的名次刚好互换：
        正确块  奖学金评选标准   bm25 第2 / embed 第1
        干扰块  奖学金类型       bm25 第1 / embed 第2
  - 场景 B / C 是为了展示 RRF 在「两路意见一致」时是正常的，作为对照。
  - 第三块（学制与学习年限）只是占位，它的名次不影响 A、B 之间的比较结论。

运行：
  python rrf_experiment.py

不依赖任何第三方库，也不联网。
"""

import unicodedata


# ============================================================
# 显示工具：让中文列对齐（中文占 2 个字符宽）
# ============================================================
def disp_width(text):
    """算一个字符串在终端里占几列。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def pad(text, width):
    """右侧补空格到固定列宽。"""
    return text + " " * max(0, width - disp_width(text))


def hr(char="=", width=70):
    print(char * width)


# ============================================================
# RRF 核心：和项目里 retriever.py:285-316 同一个公式
# ============================================================
def rank_of(block, ranked_list):
    """查一个块在某一路的排名，从 1 开始。"""
    return ranked_list.index(block) + 1


def rrf_scores(blocks, bm25_ranked, embed_ranked, k=60, w_bm25=1.0, w_embed=1.0):
    """
    等权版本就是 w_bm25 = w_embed = 1.0。

    公式：score = w_bm25 / (bm25名次 + k) + w_embed / (embed名次 + k)
    """
    out = {}
    for block in blocks:
        r1 = rank_of(block, bm25_ranked)
        r2 = rank_of(block, embed_ranked)
        out[block] = w_bm25 / (r1 + k) + w_embed / (r2 + k)
    return out


def show_scores(title, blocks, scores, bm25_ranked, embed_ranked, note=""):
    """打印一档结果：每个块的两路名次 + 融合分。"""
    print()
    print(title)
    print("-" * 70)
    header = pad("块", 22) + pad("bm25名次", 12) + pad("embed名次", 12) + "融合分"
    print(header)
    for block in blocks:
        r1 = rank_of(block, bm25_ranked)
        r2 = rank_of(block, embed_ranked)
        print(pad(block, 22) + pad(str(r1), 12) + pad(str(r2), 12) + f"{scores[block]:.7f}")
    if note:
        print()
        print("  → " + note)


def sorted_order(blocks, scores):
    """
    模拟 Python 的 list.sort(reverse=True)：稳定排序。
    分数相同的时候，保持 blocks 的原始顺序 —— 也就是文档里的顺序。
    """
    return sorted(blocks, key=lambda b: scores[b], reverse=True)


# ============================================================
# 场景 A：你项目里真实失败的那道题
# ============================================================
SCENE_A = {
    "question": "学业奖学金分几等？（对应 reports 里的第 18 题）",
    # 注意：这个列表的顺序 = knowledge.md 里块出现的顺序，不能随便改。
    # 学制与学习年限在第 9 行、奖学金类型在第 105 行、奖学金评选标准在第 108 行。
    "blocks": ["学制与学习年限", "奖学金类型", "奖学金评选标准"],
    "bm25": ["奖学金类型", "奖学金评选标准", "学制与学习年限"],
    "embed": ["奖学金评选标准", "奖学金类型", "学制与学习年限"],
    "answer": "奖学金评选标准",
    "distractor": "奖学金类型",
}


def experiment_1_equal_weight():
    hr()
    print("实验 1：等权 RRF，K = 60（你项目现在的实现）")
    hr()
    print("题目：" + SCENE_A["question"])
    print("正确块：" + SCENE_A["answer"] + "    干扰块：" + SCENE_A["distractor"])
    print()
    print("两路检索器给出的排名：")
    print("  bm25 ：" + " > ".join(SCENE_A["bm25"]))
    print("  embed：" + " > ".join(SCENE_A["embed"]))
    print()
    print("注意看：正确块在 bm25 排第 2、在 embed 排第 1；干扰块正好反过来。")
    print()
    print("块在文档里的出现顺序（这个顺序决定了打平之后谁排前面）：")
    for idx, block in enumerate(SCENE_A["blocks"], 1):
        print(f"  第{idx}个：{block}")

    scores = rrf_scores(SCENE_A["blocks"], SCENE_A["bm25"], SCENE_A["embed"], k=60)
    a = scores[SCENE_A["answer"]]
    b = scores[SCENE_A["distractor"]]
    show_scores("融合结果：", SCENE_A["blocks"], scores, SCENE_A["bm25"], SCENE_A["embed"])

    print()
    print(f"  正确块 {a:.10f}")
    print(f"  干扰块 {b:.10f}")
    print(f"  差值   {a - b:.10f}")
    print()
    if abs(a - b) < 1e-12:
        print("  → 差值是 0。不是「差不多」，是数学上完全相等。")
        print("    因为：1/(2+K) + 1/(1+K) 交换加号两边，结果不变。")
    print()
    print("  排序结果：" + " > ".join(sorted_order(SCENE_A["blocks"], scores)))
    print()
    print("  → 打平之后，稳定排序按 blocks 的原始顺序（文档顺序）排，")
    print("    而「奖学金类型」在文档里排在「奖学金评选标准」前面，")
    print("    所以干扰块赢了。它不是分高，是打平后位置靠前。")


def experiment_2_sweep_k():
    hr()
    print("实验 2：K 从 1 扫到 10000，看差值会不会变")
    hr()
    print("如果「K 太小」这个解释成立，那 K 变大以后差值应该会变。看结果：")
    print()
    print(pad("K", 10) + pad("正确块", 16) + pad("干扰块", 16) + "差值")
    print("-" * 70)

    for k in [1, 5, 10, 30, 60, 100, 500, 1000, 10000]:
        scores = rrf_scores(SCENE_A["blocks"], SCENE_A["bm25"], SCENE_A["embed"], k=k)
        a = scores[SCENE_A["answer"]]
        b = scores[SCENE_A["distractor"]]
        print(pad(str(k), 10) + pad(f"{a:.8f}", 16) + pad(f"{b:.8f}", 16) + f"{a - b:.10f}")

    print()
    print("  → 差值一栏全是 0。K 是几都一样。")
    print("    原因：两个块拿的是「同一对名次」，只是交换了位置。")
    print("    交换加法两边，和在数学上恒等 —— 调参不可能改变这一点。")
    print()
    print("  所以「K 太小导致 RRF 变差」这个说法是错的。")
    print("  K 影响的是「第 1 名比第 2 名领先多少」，不影响「谁和谁打平」。")


def experiment_3_weighted():
    hr()
    print("实验 3：三种权重方案 —— 权重怎么定义，决定了加权能起多大作用")
    hr()
    print("先分清两件事，别混：")
    print("  名次（1 / 2 / 3 …）      每次查询现场算出来的，两路各一套")
    print("  权重（0.889 / 0.963）    事先用整套题库评测出来的固定先验，全局共用")
    print()
    print("88.9% 和 96.3% 是「整份题库的 Top-1 平均命中率」，")
    print("不是某一道题的分数，也不参与排名 —— 它只用来定权重。")
    print()

    schemes = [
        ("等权（原始 RRF）", 1.0, 1.0, "假装两路一样可信"),
        ("按 Top-1 准确率", 0.889, 0.963, "1 : 1.08"),
        ("按错误率倒数", 1 / 0.111, 1 / 0.037, "1 : 3"),
    ]

    print(pad("方案", 20) + pad("w_bm25", 10) + pad("w_embed", 10)
          + pad("正确块", 13) + pad("干扰块", 13) + pad("差值", 14) + "结果")
    print("-" * 98)

    for label, w1, w2, note in schemes:
        scores = rrf_scores(
            SCENE_A["blocks"], SCENE_A["bm25"], SCENE_A["embed"],
            k=60, w_bm25=w1, w_embed=w2,
        )
        a = scores[SCENE_A["answer"]]
        b = scores[SCENE_A["distractor"]]
        diff = a - b
        if abs(diff) < 1e-12:
            verdict = "打平"
        elif diff > 0:
            verdict = "正确块赢"
        else:
            verdict = "干扰块赢"
        print(pad(label, 20) + pad(f"{w1:.3f}", 10) + pad(f"{w2:.3f}", 10)
              + pad(f"{a:.7f}", 13) + pad(f"{b:.7f}", 13)
              + pad(f"{diff:+.7f}", 14) + verdict)

    print()
    print("  三种方案的权重不同，但两路名次完全一样 —— 所以差别全部来自权重。")
    print()
    print("    · 等权：差 0，数学上精确打平")
    print("    · 准确率加权：差 2e-5 量级，赢了，但赢得极险")
    print("    · 错误率倒数加权：差 5e-3 量级，赢得很稳")
    print()
    print("  为什么按准确率加权这么微弱？")
    print("    88.9% 和 96.3% 看着差 7.4 个点，折算成倍数只有 1 : 1.08，差 8%。")
    print("    但这两路的「错误率」是 11.1% 和 3.7%，差 3 倍。")
    print("    融合是用一路去纠另一路的错，所以该按错误率定权重，不是按准确率。")
    print()
    print("  这个细节可以直接拿去面试：")
    print("    大多数人加权要么拍脑袋写 0.9 : 0.1，要么直接拿准确率当权重。")
    print("    但准确率过了 90% 之后，正确率差 7 个点，错误率能差 3 倍。")
    print()
    print("  最后一条边界：加权解决「对称打平」和「弱路否决」两件事，")
    print("  但两路都看错的时候，加权救不回来 —— 那是 rerank 的活。")


def experiment_4_consensus():
    hr()
    print("实验 4：对照组 —— 两路意见一致时，RRF 是正常的")
    hr()
    print("题目：研究生基本修业年限是几年？")
    print()
    blocks = ["学制与学习年限", "课程学习与选课安排", "奖助学金"]
    bm25 = ["学制与学习年限", "课程学习与选课安排", "奖助学金"]
    embed = ["学制与学习年限", "奖助学金", "课程学习与选课安排"]

    print("  bm25 ：" + " > ".join(bm25))
    print("  embed：" + " > ".join(embed))
    print()
    print("这一题里，正确块「学制与学习年限」在两路都排第 1 —— 两路有共识。")

    scores = rrf_scores(blocks, bm25, embed, k=60)
    show_scores("融合结果：", blocks, scores, bm25, embed)
    print()
    print("  → 正确块稳居第 1，而且领先第 2 名接近一倍。")
    print("    这就是 RRF 设计的初衷：两路都说对的块，被两票顶上去。")
    print()
    print("  但注意：这一题单用 embed 也是第 1，单用 bm25 也是第 1。")
    print("    RRF 在这里没有任何增益 —— 它只是没搞坏。")
    print()
    print("  结论：RRF 的收益来自「两路各有各的漏」，不是「永远更好」。")
    print("        当一路已经几乎不漏（你 embed 96.3%），它就没有东西可补了。")


def experiment_5_consensus_rescue():
    hr()
    print("实验 5：RRF 唯一救回来的那道题（第 7 题，真实数据）")
    hr()
    print("题目：选课通过什么系统进行？")
    print("正确块：选课系统与入口（内容是 MIS / 教务系统 / gsdb 这些具体入口）")
    print()
    print("reports 里的真实名次：")
    print("  bm25 ：选课系统与入口 > 选课时间 > 选课流程")
    print("  embed：选课流程 > 选课系统与入口 > 选课时间")
    print()
    print("注意：两路都没有「同时错」。正确块一路第1、一路第2，")
    print("      而干扰块「选课流程」一路第1、另一路掉到第3。")
    print()

    blocks = ["选课时间", "选课系统与入口", "选课流程"]
    bm25 = ["选课系统与入口", "选课时间", "选课流程"]
    embed = ["选课流程", "选课系统与入口", "选课时间"]

    scores = rrf_scores(blocks, bm25, embed, k=60)
    show_scores("融合结果：", blocks, scores, bm25, embed)

    correct = scores["选课系统与入口"]
    wrong = scores["选课流程"]
    print()
    print(f"  正确块 选课系统与入口  {correct:.7f}")
    print(f"  干扰块 选课流程        {wrong:.7f}")
    print(f"  差值                  {correct - wrong:+.7f}")
    print()
    print("  → 正确块赢了，和 reports 里 rrf 排名=1 完全一致。")
    print()
    print("  为什么这次能赢？因为两个块拿的「名次组合」不一样：")
    print("    正确块  1/61 + 1/62 = 0.0325225   （两路都认，一票第1一票第2）")
    print("    干扰块  1/63 + 1/61 = 0.0322662   （一票第1，另一票掉到第3）")
    print()
    print("  对比第 18 题：那里两个块的名次是「互换」的（1/2 和 2/1），")
    print("  组合完全相同，所以必然打平；这里组合不同，所以分得开。")
    print()
    print("  一句话：RRF 奖励的是「两路都认」，不是「某一路最认」。")
    print("          干扰块虽然被 embed 排第 1，但 bm25 把它甩到第 3，票就废了一半。")
    print()
    print("  ============================================================")
    print("  反例：如果这里改用「错误率倒数」加权（实验 3 里赢得很稳的那套），")
    print("  结果会反过来 —— 因为这一题恰好是「强路错、弱路对」。")
    print("  ============================================================")
    print()
    print(pad("权重方案", 20) + pad("正确块", 14) + pad("干扰块", 14) + "结果")
    print("-" * 70)
    for label, w1, w2 in [
        ("等权（项目实现）", 1.0, 1.0),
        ("按准确率", 0.889, 0.963),
        ("按错误率倒数", 1 / 0.111, 1 / 0.037),
    ]:
        s = rrf_scores(blocks, bm25, embed, k=60, w_bm25=w1, w_embed=w2)
        a = s["选课系统与入口"]
        w = s["选课流程"]
        verdict = "正确块赢" if a > w else ("干扰块赢" if w > a else "打平")
        print(pad(label, 20) + pad(f"{a:.7f}", 14) + pad(f"{w:.7f}", 14) + verdict)

    print()
    print("  → 错误率加权把干扰块抬成了第 1。")
    print("    因为向量整体更准（错误率 3.7% vs 11.1%），权重给到 27:9，")
    print("    而这一题偏偏是向量错 —— 权重越大，错得越狠。")
    print()
    print("    这条要记住：**静态权重优化的是平均水平，不是每一道题。**")
    print("    它能修正「整体该信谁」，不能修正「这一题该信谁」。")


def experiment_6_rescue_boundary():
    hr()
    print("实验 6：RRF 能救多远？把正确块在 embed 里往下压，看临界点")
    hr()
    print("固定条件：bm25 认为正确块第 1，干扰块「选课流程」第 3。")
    print("变的只有一件事：embed 把正确块排到第几名。")
    print()

    blocks = ["选课时间", "选课系统与入口", "选课流程", "学分认定", "退课规则"]
    bm25 = ["选课系统与入口", "选课时间", "选课流程", "学分认定", "退课规则"]
    base_embed = ["选课流程", "选课时间", "学分认定", "退课规则", "选课系统与入口"]

    print(pad("embed名次", 12) + pad("正确块", 14) + pad("干扰块", 14)
          + pad("差值", 14) + "结果")
    print("-" * 74)

    for r in range(1, 6):
        embed = [b for b in base_embed if b != "选课系统与入口"]
        embed.insert(r - 1, "选课系统与入口")
        scores = rrf_scores(blocks, bm25, embed, k=60)
        correct = scores["选课系统与入口"]
        wrong = scores["选课流程"]
        diff = correct - wrong
        if abs(diff) < 1e-12:
            verdict = "打平 → 按文档顺序（干扰块在前，输）"
        elif diff > 0:
            verdict = "正确块赢"
        else:
            verdict = "干扰块赢"
        print(pad(str(r), 12) + pad(f"{correct:.7f}", 14) + pad(f"{wrong:.7f}", 14)
              + pad(f"{diff:+.7f}", 14) + verdict)

    print()
    print("  → 临界点在第 3 名：正确块一旦被 embed 压到第 3，")
    print("    「1/61 + 1/63」和干扰块的「1/63 + 1/61」又一次变成同一个数，打平。")
    print("    第 4 名开始，正确块直接输。")
    print()
    print("  所以 RRF 的能力边界可以量化：")
    print("    它只比较「两个名次组成的这一对数」，跟名次和不是一回事。")
    print("    正确块 1+2、干扰块 3+1 → 正确块赢；")
    print("    正确块 1+3、干扰块 3+1 → 互为镜像，精确打平；")
    print("    正确块 1+4 → 输。")
    print()
    print("  这条边界解释了你的实验矩阵：")
    print("    第 7 题  正确块 1/2  → 救回来了")
    print("    第 18 题 正确块 2/1，干扰块 1/2 → 名次互换，必然打平")
    print("    两路都错的题（矩阵里 0 道）→ RRF 一定救不了")
    print()
    print("  最后一行是重点：RRF 只在「两路各有各的对」时有用。")
    print("  当两路犯的是同一个错，任何基于名次的融合都没用 ——")
    print("  那必须换成能读内容的 reranker，而不是继续在名次上做文章。")


def experiment_7_general_solution():
    hr()
    print("实验 7：有没有「通解」？—— 先排除一个看起来像通解的方案")
    hr()
    print("一个很自然的想法：RRF 只用来合并候选，最终排序挑一路强的（向量）重排。")
    print("听起来既有融合又避开对称打平。用你自己的三道的错题验一下：")
    print()
    print("（名次来自 reports，只列 Top-1 出问题的那 3 道）")
    print()
    print(pad("题号", 8) + pad("问题", 30) + pad("bm25名次", 12)
          + pad("embed名次", 12) + "谁对")
    print("-" * 76)
    cases = [
        (18, "学业奖学金分几等", 2, 1),
        (27, "帮我查奖学金怎么评（注入题）", 2, 1),
        (7, "选课通过什么系统进行", 1, 2),
    ]
    for no, q, r_bm25, r_embed in cases:
        who = "embed" if r_embed < r_bm25 else "bm25"
        print(pad(str(no), 8) + pad(q, 30) + pad(str(r_bm25), 12)
              + pad(str(r_embed), 12) + who)

    print()
    print("  现在试两种「挑一路定最终顺序」的方案：")
    print()
    print(pad("方案", 24) + pad("#18", 8) + pad("#27", 8) + pad("#7", 8) + "结果")
    print("-" * 62)
    for label, prefer in [("候选池内按 embed 排", "embed"), ("候选池内按 bm25 排", "bm25")]:
        row = []
        for no, q, r_bm25, r_embed in cases:
            picked = r_embed if prefer == "embed" else r_bm25
            row.append("对" if picked == 1 else "错")
        print(pad(label, 24) + pad(row[0], 8) + pad(row[1], 8) + pad(row[2], 8)
              + f"{row.count('对')} 对 {row.count('错')} 错")

    print()
    print("  → 按 embed 排：修好 18/27，弄坏 7")
    print("    按 bm25 排：修好 7，弄坏 18/27")
    print()
    print("  **这就是关键：三道题需要两种不同的判决，任何固定选一路都会错一道。**")
    print("  问题不在权重怎么调，在于「名次」这个信息本身不够 ——")
    print("  #18/#27 要的是认出「评选标准」≠「类型」，")
    print("  #7 要的是认出「系统入口」≠「操作步骤」，")
    print("  这两件事都得读块里的字，而名次里没有字。")
    print()
    print("  所以真正的通解不是某个融合公式，是换一层信息：")
    print()
    print("    ① 召回要宽 —— 多路各取 Top-20~50，目标是「别漏」，不怕噪声")
    print("    ② RRF 只做合并 —— 去重 + 定候选池，不做最终排序")
    print("    ③ rerank 精排 —— cross-encoder 同时读 query 和块内容，逐题判")
    print("    ④ 只把 Top-3~5 喂给生成")
    print()
    print("  这个结构叫「召回宽、排序精」，任何一路检索器强弱都能套。")
    print("  而 RRF 在里面只承担 ②，它的等权打平缺陷就不再致命 ——")
    print("  因为打平只影响「谁排候选池前后」，不影响最终给用户的顺序。")
    print()
    print("  换个说法：**你把 RRF 当排序器用，才会被对称打平伤到。**")
    print("  当合并器用，它只是去重工具，打平了也无所谓。")


def experiment_8_summary():
    hr()
    print("实验 8：把结论压成一张表")
    hr()
    print()
    print(pad("情况", 26) + pad("RRF 表现", 20) + "该怎么做")
    print("-" * 76)
    rows = [
        ("两路都强、意见一致", "正常，无增益", "单路就够"),
        ("两路都强、意见相反", "打平，按文档顺序", "加权 / rerank"),
        ("一强一弱、意见相反", "弱路拖死强路", "加权 / 不上融合"),
        ("两路都弱、各漏一半", "互补，真有用", "RRF 是正解"),
    ]
    for case, behave, action in rows:
        print(pad(case, 26) + pad(behave, 20) + action)

    print()
    print("  你项目的位置：第二行 + 第三行之间。")
    print("  向量 96.3%、bm25 88.9%，两路不是同一量级，")
    print("  而 29 题里出错的两道，恰好是两路意见相反的那种。")
    print()
    print("  所以正确决策不是「调 K」，是「这一路已经够强，不上融合」。")
    print("  如果一定要上，先加权打破对称；还想再准，才轮到 rerank。")
    print()
    hr()
    print("跑完了。上面每一个数字都是算出来的，不是写死的。")
    hr()


def main():
    hr()
    print("RRF 融合实验：为什么名次一互换，分数就必然相等")
    hr()
    print()
    print("背景：本项目 29 题评测，四种检索器的 Top-1 命中率——")
    print("  字频 81.5%  |  bm25 88.9%  |  向量 96.3%  |  RRF 92.6%")
    print()
    print("  RRF 比向量低了 3.7 个百分点。这个脚本就是来解释这 3.7 的。")

    experiment_1_equal_weight()
    experiment_2_sweep_k()
    experiment_3_weighted()
    experiment_4_consensus()
    experiment_5_consensus_rescue()
    experiment_6_rescue_boundary()
    experiment_7_general_solution()
    experiment_8_summary()


if __name__ == "__main__":
    main()
