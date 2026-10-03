# -*- coding: utf-8 -*-
"""
Web demo：把命令行问答换成一个网页。

作用：让面试官「点开链接就能问问题」，不用 clone、不用配密钥、不用跑命令行。
核心逻辑（ReAct 循环 / 检索 / 生成）一行没改，只是把 app.py 的 input() 换成
浏览器里的输入框，把 print 换成网页上的文字。

跑法：
    python web_demo.py
然后浏览器打开 http://127.0.0.1:8000

依赖：Flask（pip install flask），其余复用项目现有代码。
"""

import sys
from pathlib import Path

# 把 src/ 加进模块搜索路径，才能 import config / agent 等
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from flask import Flask, request, jsonify, render_template
import config
import memory
from agent import KBAgent

app = Flask(__name__)

# 一个全局 Agent 实例，启动时建一次（读知识库 + 历史），所有请求共用。
# 这样连续提问能接上上下文，跟命令行版体验一致。
agent = KBAgent()
agent.load_history()


def _hits_payload():
    """把上一轮命中的知识块整理成前端能直接渲染的列表。"""
    out = []
    for i, h in enumerate(agent.last_hits):
        b = h["block"]
        out.append({
            "rank": i + 1,
            "score": round(h["score"], 4),
            "title": b.get("title", ""),
            "text": b.get("text", ""),
        })
    return out


@app.route("/")
def index():
    """首页：渲染 templates/index.html。"""
    return render_template("index.html",
                           blocks=len(agent.blocks),
                           retriever=config.RETRIEVER,
                           top_k=config.TOP_K,
                           mode=config.AGENT_MODE)


@app.route("/ask", methods=["POST"])
def ask():
    """问一个问题：body 里传 {"question": "..."}，返回答案 + 命中的块。"""
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    try:
        answer = agent.ask(question)
    except Exception as e:
        return jsonify({"error": "%s: %s" % (type(e).__name__, e)}), 500

    return jsonify({
        "answer": answer,
        "hits": _hits_payload(),
    })


@app.route("/clear", methods=["POST"])
def clear():
    """清空记忆，从头开始。"""
    memory.clear()
    agent.messages = [agent.messages[0]]
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("=" * 60)
    print("  campus-rag-agent · Web demo")
    print("  浏览器打开  http://127.0.0.1:8000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=8000, debug=False)
