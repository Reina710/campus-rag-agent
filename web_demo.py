# -*- coding: utf-8 -*-
"""
Web 服务：把命令行问答换成一个网页。

和上一版的区别（都是为了能上线）：
    1. 不再用一个全局 agent —— 按 cookie 里的会话号分流，两个人互不干扰。
    2. 加了限流：同一 IP 每分钟最多问几次，防止被脚本刷爆账单。
    3. 加了日志：每个请求记一行，出问题能查。
    4. 加了 /health：部署平台靠它判断服务是否活着。

跑法：
    python web_demo.py                 # 开发
    gunicorn -w 1 --threads 16 web_demo:app   # 生产（见 Dockerfile）

为什么是单 worker 多线程：会话状态在进程内存里，多 worker 时请求可能
落到没有该会话的进程上，用户会莫名丢历史。要么单进程，要么把会话挪到
Redis——这个项目规模选前者。
"""

import logging
import secrets
import sys
import time
from pathlib import Path

# 把 src/ 加进模块搜索路径，才能 import config / agent 等
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from flask import Flask, request, jsonify, render_template, session
import config
import knowledge
import memory
from ratelimit import RateLimiter
from sessions import SessionStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("kb.web")


app = Flask(__name__)

# cookie 签名密钥。没配 APP_SECRET 就每次启动随机生成，
# 代价是重启后所有人都掉会话（演示环境可以接受）。
app.secret_key = config.APP_SECRET or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,     # JS 读不到，降低 XSS 风险
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=8 * 1024,      # 请求体上限 8KB，防大包
)

store = SessionStore()
limiter = RateLimiter(config.RATE_LIMIT_PER_MIN)
STARTED_AT = time.time()

# 启动时清一次旧记忆文件：每个访客都会留一个文件，不清就只涨不降
try:
    _purged = memory.purge_older_than(7)
    if _purged:
        log.info("清理了 %d 个过期会话文件", _purged)
except OSError:
    pass

MAX_QUESTION_CHARS = 200


def _sid():
    """取当前会话号，没有就发一个。"""
    sid = session.get(config.SESSION_COOKIE)
    if not sid:
        sid = secrets.token_urlsafe(16)
        session[config.SESSION_COOKIE] = sid
    return sid


def _client_ip():
    """客户端 IP。

    反向代理后面，真实 IP 在 X-Forwarded-For 里；但这个头客户端自己也能写，
    默认不信任，否则换个头就绕过限流了。只有确认在代理后面才开 TRUST_PROXY。
    """
    if config.TRUST_PROXY:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _hits_payload(agent):
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


@app.after_request
def _log_request(resp):
    log.info("path=%s status=%s cost=%.2fs ip=%s",
             request.path, resp.status_code,
             time.time() - getattr(request, "_started", time.time()),
             _client_ip())
    return resp


@app.before_request
def _mark_start():
    request._started = time.time()


@app.route("/")
def index():
    """首页：渲染 templates/index.html。"""
    # 首页就建会话，保证后续 /ask 用的 agent 和页面显示的一致
    sess = store.get(_sid())
    return render_template("index.html",
                           blocks=len(sess.agent.blocks),
                           retriever=config.RETRIEVER,
                           top_k=config.TOP_K,
                           mode=config.AGENT_MODE)


@app.route("/ask", methods=["POST"])
def ask():
    """问一个问题：body 里传 {"question": "..."}，返回答案 + 命中的块。"""
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()

    # 限流放在参数校验之前：恶意请求往往是"大量畸形请求"，
    # 先校验就会让它们白跑一遍逻辑，还占不到配额。
    allowed, retry_after = limiter.allow(_client_ip())
    if not allowed:
        log.warning("rate limited ip=%s", _client_ip())
        return jsonify({"error": "问得有点快，歇 %d 秒再来" % retry_after}), 429

    if not question:
        return jsonify({"error": "问题不能为空"}), 400
    if len(question) > MAX_QUESTION_CHARS:
        return jsonify({"error": "问题太长了，请控制在 %d 字以内" % MAX_QUESTION_CHARS}), 400

    sess = store.get(_sid())
    t0 = time.time()
    # 同一会话串行：用户连点两次发送时，两个请求不能同时改 messages
    with sess.lock:
        answer = sess.agent.ask(question)
        sess.turns += 1
        hits = _hits_payload(sess.agent)

    log.info("ask session=%s turns=%d cost=%.2fs len=%d",
             sess.sid, sess.turns, time.time() - t0, len(question))
    return jsonify({"answer": answer, "hits": hits})


@app.route("/clear", methods=["POST"])
def clear():
    """清空当前会话的记忆，从头开始。"""
    sid = _sid()
    memory.clear(sid)
    store.drop(sid)
    return jsonify({"ok": True})


@app.route("/health")
def health():
    """健康检查：部署平台和监控都靠它。

    总返回 200，用 status 字段表达状态——如果依赖缺失就返回 503，
    很多平台会直接把容器重启，而"没配 key"重启一百次也好不了。
    """
    degraded = not (config.DEEPSEEK_KEY and config.DASHSCOPE_KEY)
    return jsonify({
        "status": "degraded" if degraded else "ok",
        "uptime_seconds": int(time.time() - STARTED_AT),
        "blocks": len(knowledge.get_blocks()),
        "retriever": config.RETRIEVER,
        "llm_configured": bool(config.DEEPSEEK_KEY),
        "embedding_configured": bool(config.DASHSCOPE_KEY),
        "sessions": store.stats(),
    })


if __name__ == "__main__":
    print("=" * 60)
    print("  campus-rag-agent · Web")
    print("  http://127.0.0.1:%d" % config.PORT)
    print("=" * 60)
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)
