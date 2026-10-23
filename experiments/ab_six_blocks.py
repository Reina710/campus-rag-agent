"""受控实验：把 6 个块临时换回原版，在同一批题上对比补全前后。

只统计 expect 落在这 6 个块上的题——它们才是这次改动的直接受益者。
整体 199 题里它们只占 8.5%，混在总分里当然看不出区别。
"""
import io
import re
import sys

sys.path.insert(0, "src")
import config
import knowledge
import retriever as R
from question_bank import QUESTIONS
from eval_pipeline import hit_pos, expects_of

KB = "data/knowledge.md"
TARGETS = {"竞赛类型", "竞赛加分", "交换生项目", "国际会议",
           "选择导师", "实验室日常"}

OLD_BODIES = {
    "竞赛类型": "研究生可以参加各类学科竞赛，如数学建模、程序设计竞赛、创新创业大赛等。"
               "获奖对评奖评优和求职都有帮助。",
    "竞赛加分": "竞赛获奖在奖学金评选、保研、求职中都是重要加分项。国家级奖项的含金量远高于校级。",
    "交换生项目": "学校有国际交换生项目，可以到合作院校交流学习。"
                 "交换期间的学分可以认定，但需要提前规划课程安排。",
    "国际会议": "参加国际学术会议可以拓展视野、积累学术资源。部分学院对参会提供经费支持。",
    "选择导师": "选择导师要考虑研究方向、课题组氛围、导师指导风格等因素。"
               "建议提前了解导师的研究方向和毕业生去向。",
    "实验室日常": "进入实验室后，需要遵守实验室的规章制度，参加组会，按时完成导师布置的任务。"
                 "科研进度和导师保持沟通。",
}


def patch_section(raw, title, body):
    """把小节正文替换成 body（到下一个 ### 或 ## 为止）。"""
    pat = re.compile(r"(### " + re.escape(title) + r"\n)(.*?)(?=\n### |\n## |\Z)", re.S)
    m = pat.search(raw)
    if not m:
        raise KeyError("找不到小节：" + title)
    return raw[:m.start(2)] + body + raw[m.end(2):]


def eval_17(blocks, label, use_rerank):
    items = [q for q in QUESTIONS
             if any(e in TARGETS for e in expects_of(q))]
    rows = []
    for it in items:
        q = it["q"]
        bm_full = R.bm25_search(q, blocks, len(blocks))
        em_full = R.embed_search(q, blocks, len(blocks))
        bm = bm_full[:3]
        em = em_full[:3]
        rrf = R._rrf_merge(bm_full, em_full, blocks)[:3]

        if use_rerank:
            from rerank import rerank as do_rerank
            pool = R.retrieve_pool(q, blocks)
            hy = do_rerank(q, pool) or pool
            hy = hy[:3]
        else:
            hy = None

        rows.append({
            "q": q,
            "bm25": hit_pos(bm, it),
            "embed": hit_pos(em, it),
            "rrf": hit_pos(rrf, it),
            "hybrid": hit_pos(hy, it) if hy else None,
        })
    return items, rows


def summary(rows, key):
    hit = sum(1 for r in rows if r[key] == 1)
    found = sum(1 for r in rows if r[key])
    mrr = sum(1.0 / r[key] for r in rows if r[key]) / len(rows)
    return hit * 100.0 / len(rows), found * 100.0 / len(rows), mrr


def main():
    raw_new = io.open(KB, encoding="utf-8").read()
    raw_old = raw_new
    for t, b in OLD_BODIES.items():
        raw_old = patch_section(raw_old, t, b)

    blocks_old = knowledge.split_by_heading(raw_old)
    blocks_new = knowledge.load_blocks()
    print("旧版块数 %d   新版块数 %d" % (len(blocks_old), len(blocks_new)))

    out = []
    for tag, blocks in (("补全前", blocks_old), ("补全后", blocks_new)):
        items, rows = eval_17(blocks, tag, use_rerank=True)
        out.append((tag, items, rows))
        print("\n===== %s（%d 题）=====" % (tag, len(rows)))
        for key in ("bm25", "embed", "rrf", "hybrid"):
            t1, t3, mrr = summary(rows, key)
            print("  %-7s Top-1 %5.1f%%   Top-3 %5.1f%%   MRR %.3f"
                  % (key, t1, t3, mrr))

    # 逐题对照
    print("\n===== 逐题 Top-1 对照（x = 前三都没命中）=====")
    a_rows = out[0][2]
    b_rows = out[1][2]
    for ra, rb in zip(a_rows, b_rows):
        cells = []
        for key in ("bm25", "embed", "rrf", "hybrid"):
            x = ra[key] or "x"
            y = rb[key] or "x"
            mark = "" if x == y else "  *"
            cells.append("%s%s→%s" % (key[:4], x, y) + mark)
        print("  %-32s %s" % (ra["q"][:30], "  ".join(cells)))

    # 回答质量对照：拿两道题走完整链路
    config.SHOW_THINKING = False
    config.SUGGEST_FOLLOWUP = False
    from agent import KBAgent
    for tag, blocks in (("补全前", blocks_old), ("补全后", blocks_new)):
        print("\n===== %s 的回答 =====" % tag)
        agent = KBAgent(session_id="ab_%s" % ("old" if tag == "补全前" else "new"))
        agent.blocks = blocks
        for q in ("参加国际会议学院给报销吗？", "怎么了解一个导师值不值得选？"):
            ans = agent.ask(q)
            print("[%s]\n%s\n" % (q, ans.strip()[:300]))


if __name__ == "__main__":
    main()
