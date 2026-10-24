# -*- coding: utf-8 -*-
"""
LLM-as-judge 评测：用模型给"回答质量"打分，补上关键词匹配测不出的部分。

为什么需要这一层：
    evaluate.py 的"回答通过率"靠关键词（keys）匹配——回答里出现 "40" 就算过。
    但它测不出三件事：
        1. 答非所问（关键词碰巧出现，但没回答问题）
        2. 编造（资料里没有的内容，模型自己补的）
        3. 漏限定（比如"最多 3 个月"答成"3 个月"）

    所以再加一个模型当裁判，按两个维度打分：
        faithfulness  回答是否只依据资料（1-5，5 = 完全有据，1 = 明显编造）
        relevance     回答是否真的回答了问题（1-5）

用法：
    python src/eval_judge.py --limit 20        # 先跑 20 题试水
    python src/eval_judge.py --split holdout   # 只跑独立评测集
    python src/eval_judge.py                   # 全量（199 题，慢且花钱）

设计取舍：
    - 用 rag 模式（检索一次、生成一次），不用 Agent 循环：裁判要评"答案质量"，
      不希望被工具调用的随机性干扰。
    - 裁判和答题用同一个模型，但温度 0、提示词独立，尽量降低"自己夸自己"的倾向。
    - 每题两次调用（答 + 判），全量约 400 次，所以默认只跑 20 题。
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import knowledge
import retriever
import question_bank
from openai import OpenAI


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.DEEPSEEK_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
            timeout=config.LLM_TIMEOUT,
            max_retries=config.LLM_MAX_RETRIES,
        )
    return _client


ANSWER_SYSTEM = """你是一个校园信息问答助手。只根据【资料】回答，资料里没写的直接说「资料里没写」。
回答控制在三句话以内，直接说结论。"""

JUDGE_SYSTEM = """你是答案质量裁判。根据【问题】【资料】【回答】，给两个 1-5 分：

faithfulness（忠实度）：
  5 = 每个结论都能在资料里找到依据
  3 = 大部分有据，有少量含糊
  1 = 明显编造/资料里没有的内容

relevance（相关性）：
  5 = 直接、完整地回答了问题
  3 = 沾边，但漏了关键点
  1 = 答非所问

只输出 JSON，不要任何解释性文字：
{"faithfulness": 4, "relevance": 5, "reason": "一句话理由"}"""


def _chat(system, user):
    resp = _get_client().chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()


def _parse_json(text):
    """裁判可能把 JSON 包在 ```json 里，剥掉再解析。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(t[start:end + 1])
    except ValueError:
        return None


def _select(split):
    if split == "dev":
        return question_bank.dev_questions()
    if split == "holdout":
        return question_bank.holdout_questions()
    return question_bank.QUESTIONS


def _get_question(base):
    """把 expect 变成"给模型看的正确块提示"（裁判不需要，但答案生成要 context）。"""
    return base


def judge_one(blocks, item):
    """答一遍 + 判一遍，返回 (answer, score_dict)。"""
    question = item["q"]
    if item["expect"] == "（无）":
        # 陷阱题：不检索，直接看它会不会编
        answer = _chat(ANSWER_SYSTEM, "【问题】%s\n\n【资料】\n（没有检索到相关资料）" % question)
        score = _parse_json(_chat(JUDGE_SYSTEM,
                                  "【问题】%s\n【资料】\n（无）\n【回答】\n%s" % (question, answer))) or {}
        return answer, score

    hits = retriever.search(question, blocks, config.TOP_K)
    context = "\n\n".join("【%s】\n%s" % (h["block"]["title"], h["block"]["text"]) for h in hits)
    answer = _chat(ANSWER_SYSTEM, "【问题】%s\n\n【资料】\n%s" % (question, context))
    score = _parse_json(_chat(JUDGE_SYSTEM,
                              "【问题】%s\n【资料】\n%s\n【回答】\n%s" % (question, context, answer))) or {}
    return answer, score


def main():
    limit = 20
    split = "all"
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
        if arg == "--split" and i + 1 < len(sys.argv):
            split = sys.argv[i + 1]

    items = _select(split)
    if limit:
        items = items[:limit]

    blocks = knowledge.load_blocks()
    print("=" * 70)
    print("LLM-as-judge 评测   子集=%s   题数=%d   知识库=%d 块" % (split, len(items), len(blocks)))
    print("=" * 70)

    rows = []
    for i, it in enumerate(items, 1):
        try:
            answer, score = judge_one(blocks, it)
        except Exception as e:
            print("[%2d/%d] %-24s 失败: %s" % (i, len(items), it["q"][:20], e))
            continue
        f = score.get("faithfulness")
        r = score.get("relevance")
        rows.append((it["q"], f, r, answer, score.get("reason", "")))
        print("[%2d/%d] %-24s 忠实=%s 相关=%s" % (i, len(items), it["q"][:20], f, r))

    ok = [x for x in rows if isinstance(x[1], (int, float)) and isinstance(x[2], (int, float))]
    avg_f = sum(x[1] for x in ok) / len(ok) if ok else 0
    avg_r = sum(x[2] for x in ok) / len(ok) if ok else 0
    pass_n = sum(1 for x in ok if x[1] >= 3 and x[2] >= 3)

    print("-" * 70)
    print("有效样本：%d/%d" % (len(ok), len(rows)))
    print("平均忠实度：%.2f / 5" % avg_f)
    print("平均相关性：%.2f / 5" % avg_r)
    print("双项及格率（>=3）：%.1f%%" % (pass_n * 100.0 / len(ok) if ok else 0))
    print("=" * 70)

    os.makedirs("reports", exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("reports", "judge_%s_%s.txt" % (split, stamp))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("LLM-as-judge  split=%s  n=%d  blocks=%d\n" % (split, len(items), len(blocks)))
        fh.write("平均忠实度 %.2f  平均相关性 %.2f  双项及格 %.1f%%\n\n"
                 % (avg_f, avg_r, pass_n * 100.0 / len(ok) if ok else 0))
        for q, f, r, ans, reason in rows:
            fh.write("Q: %s\n  忠实=%s 相关=%s\n  A: %s\n  理由: %s\n\n" % (q, f, r, ans, reason))
    print("[报告已写入 %s]" % path)


if __name__ == "__main__":
    main()
