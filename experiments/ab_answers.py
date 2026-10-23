"""回答质量对照：同一道题，旧版 6 块 vs 新版 6 块，看模型能说出多少细节。

关键：工具链路走的是 kb_tools 自己的 blocks 缓存，必须一起换掉，
否则两次都读新版知识库，看起来「一模一样」。
"""
import io
import re
import sys

sys.path.insert(0, "src")
import config
import knowledge
import kb_tools
import agent as agent_mod

KB = "data/knowledge.md"
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
QS = [
    "研究生能参加哪些学科竞赛？",
    "出国交换之前要做什么准备？",
    "实验室要开组会吗？进去之后一般怎么运转？",
]


def patch_section(raw, title, body):
    pat = re.compile(r"(### " + re.escape(title) + r"\n)(.*?)(?=\n### |\n## |\Z)", re.S)
    m = pat.search(raw)
    if not m:
        raise KeyError(title)
    return raw[:m.start(2)] + body + raw[m.end(2):]


def run(blocks, tag):
    kb_tools._BLOCKS = blocks
    agent_mod._BLOCKS_CACHE = blocks
    a = agent_mod.KBAgent(session_id="ab2_%s" % tag)
    a.blocks = blocks
    out = []
    for q in QS:
        ans = a.ask(q)
        out.append("[%s]\n%s\n" % (q, ans.strip()))
    return out


raw_new = io.open(KB, encoding="utf-8").read()
raw_old = raw_new
for t, b in OLD_BODIES.items():
    raw_old = patch_section(raw_old, t, b)

blocks_old = knowledge.split_by_heading(raw_old)
blocks_new = knowledge.load_blocks()

config.SHOW_THINKING = False
config.SUGGEST_FOLLOWUP = False

old_ans = run(blocks_old, "old")
new_ans = run(blocks_new, "new")

print("=" * 78)
for q, o, n in zip(QS, old_ans, new_ans):
    print("【旧版】" + o)
    print("【新版】" + n)
    print("-" * 78)

with io.open("experiments/_ab_answers.txt", "w", encoding="utf-8") as f:
    for q, o, n in zip(QS, old_ans, new_ans):
        f.write("【旧版】" + o + "\n" + "【新版】" + n + "\n" + "-" * 60 + "\n")
