# -*- coding: utf-8 -*-
"""
Web 服务：把命令行问答换成一个网页。

为了"能对外开"，这一层负责的是：
    1. 会话隔离 —— 按 cookie 分流，两个人互不干扰（src/sessions.py）
    2. 限流 —— 同一 IP 每分钟有配额，模型调用是花钱的（src/ratelimit.py）
    3. 并发闸门 —— 同时在跑的问答有上限，超出直接 503，不排队把内存堆爆
    4. 启动自检 —— 知识库空/配置写错直接拒绝启动（src/selfcheck.py）
    5. 可观测 —— 一行一个 JSON 的日志 + /metrics（src/obs.py）
    6. 流式输出 —— /ask_stream 边生成边返回，首字延迟从数秒降到几百毫秒
    7. 健康检查分两级 —— /livez 看进程活没活，/readyz 看能不能真的服务

跑法：
    python web_demo.py                        # 开发（Flask 自带服务器）
    gunicorn -w 1 --threads 16 web_demo:app   # 生产（见 Dockerfile）

为什么是单 worker 多线程：会话状态在进程内存里，多 worker 时请求可能
落到没有该会话的进程上，用户会莫名丢历史。要么单进程，要么把会话挪到
Redis——这个项目规模选前者。
"""

import json
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path

# 把 src/ 加进模块搜索路径，才能 import config / agent 等
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from flask import Flask, Response, g, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException

import config
import knowledge
import memory
import obs
import selfcheck
from ratelimit import RateLimiter
from sessions import SessionStore

obs.setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"),
                  json_mode=os.environ.get("LOG_JSON", "1") != "0")
log = logging.getLogger("kb.web")

app = Flask(__name__)

# cookie 签名密钥。没配 APP_SECRET 就每次启动随机生成，
# 代价是重启后所有人都掉会话（演示环境可以接受）。
app.secret_key = config.APP_SECRET or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,       # JS 读不到，降低 XSS 风险
    SESSION_COOKIE_SAMESITE="Lax",      # 跨站请求不带 cookie
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,   # 只在 HTTPS 下发送
    MAX_CONTENT_LENGTH=8 * 1024,        # 请求体上限 8KB，防大包
)

store = SessionStore()
limiter = RateLimiter(config.RATE_LIMIT_PER_MIN)
STARTED_AT = time.time()
METRICS = obs.METRICS

# 并发闸门：同时在跑的问答数上限。
# 不加这个，16 个线程会被长请求占满，第 17 个请求在队列里等，
# 用户看到的是"转圈到浏览器超时"，而不是一句明确的"稍后再试"。
_ask_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_ASKS)

MAX_QUESTION_CHARS = 200

# 首页展示的评测数字：单独放一个 json，避免页面写死数字后与 README 漂移
EVAL_SUMMARY_PATH = Path(config.PROJECT_ROOT) / "data" / "eval_summary.json"


def _load_eval_summary():
    try:
        with open(EVAL_SUMMARY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        log.warning("eval_summary.json 读取失败，首页显示占位符")
        return {"questions": "-", "blocks": "-", "top1": "-", "top3": "-",
                "mrr": "-", "holdout_top1": "-", "holdout_questions": "-",
                "baseline_top1": "-", "cross_block": "-", "updated": "-"}


# ------------------------------------------------------------
# 启动自检：FATAL 直接抛异常，让容器启动失败（比半死不活好查）
# ------------------------------------------------------------
_report = selfcheck.run(strict=True)
log.info("selfcheck ok, blocks=%s retriever=%s", _report["info"].get("blocks"),
         _report["info"].get("retriever"))
for w in _report["warnings"]:
    log.warning("selfcheck warn: %s", w)

# 预热：jieba 词典、块向量。不做的话这些成本会转嫁给第一个访客。
if os.environ.get("WARMUP", "1") != "0":
    import warmup
    warmup.run()


# ------------------------------------------------------------
# 请求上下文：request_id + 耗时
# ------------------------------------------------------------
@app.before_request
def _before():
    g.rid = request.headers.get("X-Request-Id") or secrets.token_hex(6)
    g.started = time.time()


@app.after_request
def _after(resp):
    cost = time.time() - getattr(g, "started", time.time())
    METRICS.inc("http_%d" % resp.status_code)
    METRICS.observe_http(cost)
    resp.headers["X-Request-Id"] = getattr(g, "rid", "")

    # 流式响应的 after_request 在"响应头发出时"就跑了，此时正文还没生成完，
    # 这里记到的是首字节耗时。整轮耗时由生成器自己在结束事件里记。
    obs.log_event(log, "http_request",
                  path=request.path, method=request.method,
                  status=resp.status_code, cost_ms=int(cost * 1000),
                  ip=_client_ip(), rid=getattr(g, "rid", ""),
                  stream=resp.is_streamed)
    return resp


# ------------------------------------------------------------
# 统一错误处理：API 永远返回 JSON，不吐 HTML 错误页
# ------------------------------------------------------------
def _err(message, code, **extra):
    body = {"error": message, "request_id": getattr(g, "rid", "")}
    body.update(extra)
    return jsonify(body), code


@app.errorhandler(HTTPException)
def _http_error(e):
    if e.code == 429:
        return _err("问得有点快，稍后再试", 429)
    if e.code == 413:
        return _err("请求体太大", 413)
    if e.code in (404, 405):
        return _err("接口不存在或方法不对：%s %s" % (request.method, request.path), e.code)
    return _err(e.description or e.name, e.code or 500)


@app.errorhandler(Exception)
def _unhandled(e):
    log.exception("unhandled exception rid=%s", getattr(g, "rid", ""))
    METRICS.inc("unhandled_total")
    return _err("服务内部错误，请稍后再试", 500)


# ------------------------------------------------------------
# 会话与限流
# ------------------------------------------------------------
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


def _validate(data):
    """校验请求体，返回 (question, 错误响应)。"""
    question = (data.get("question") or "").strip()
    if not question:
        return None, _err("问题不能为空", 400)
    if len(question) > MAX_QUESTION_CHARS:
        return None, _err("问题太长了，请控制在 %d 字以内" % MAX_QUESTION_CHARS, 400)
    return question, None


def _rate_limit():
    """限流放在参数校验之前：恶意请求往往是"大量畸形请求"，
    先校验就会让它们白跑一遍逻辑，还占不到配额。"""
    allowed, retry_after = limiter.allow(_client_ip())
    if not allowed:
        METRICS.inc("ratelimit_hits_total")
        obs.log_event(log, "rate_limited", level=logging.WARNING,
                      ip=_client_ip(), rid=getattr(g, "rid", ""))
        return _err("问得有点快，歇 %d 秒再来" % retry_after, 429,
                    retry_after=retry_after)
    return None


# ------------------------------------------------------------
# 页面
# ------------------------------------------------------------
# 前端构建产物。frontend/ 用 Vite 构建到 static/，没有产物时回退到旧模板。
_BUILT_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


@app.route("/")
def index():
    """首页：优先返回构建产物，没构建过就回退到 templates/index.html。

    回退是为了部署时忘跑 npm build 不至于直接白屏——旧页面能用，只是不好看。
    """
    if _BUILT_INDEX.exists():
        return send_file(str(_BUILT_INDEX))
    sess = store.get(_sid())
    stats = _load_eval_summary()
    return render_template("index.html",
                           blocks=len(sess.agent.blocks),
                           retriever=config.RETRIEVER,
                           top_k=config.TOP_K,
                           mode=config.AGENT_MODE,
                           stats=stats)


# ------------------------------------------------------------
# 非流式问答（保留：脚本、评测、客户端不支持流式时用）
# ------------------------------------------------------------
@app.route("/ask", methods=["POST"])
def ask():
    """body: {"question": "..."} -> {"answer": "...", "hits": [...]}"""
    blocked = _rate_limit()
    if blocked:
        return blocked

    data = request.get_json(silent=True) or {}
    question, bad = _validate(data)
    if bad:
        return bad

    if not _ask_slots.acquire(blocking=False):
        METRICS.inc("concurrency_rejected_total")
        return _err("当前提问的人有点多，请稍后再试", 503)

    t0 = time.time()
    sess = store.get(_sid())
    try:
        # 同一会话串行：用户连点两次发送时，两个请求不能同时改 messages
        with sess.lock:
            answer = sess.agent.ask(question)
            sess.turns += 1
            hits = sess.agent.hits_payload()
    finally:
        _ask_slots.release()

    cost = time.time() - t0
    METRICS.inc("asks_total")
    METRICS.observe_ask(cost)
    if answer.startswith("抱歉，这次没能答上来"):
        METRICS.inc("ask_errors_total")
    obs.log_event(log, "ask_done", session=sess.sid, turns=sess.turns,
                  cost_ms=int(cost * 1000), question_len=len(question),
                  hits=len(hits), rid=getattr(g, "rid", ""))
    return jsonify({"answer": answer, "hits": hits, "request_id": getattr(g, "rid", "")})


# ------------------------------------------------------------
# 流式问答：SSE，每行一个 data: {json}
# ------------------------------------------------------------
def _sse(event, payload):
    """SSE 一行。data 里放 JSON，前端按 event 分派。"""
    body = {"type": event}
    if isinstance(payload, dict):
        body.update(payload)
    else:
        body["data"] = payload
    return "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"


@app.route("/ask_stream", methods=["POST"])
def ask_stream():
    """流式问答。前端用 fetch + ReadableStream 读（EventSource 只支持 GET）。

    事件流：hits -> delta* -> done，中途可能来一个 reset（撤回已显示内容）。
    """
    blocked = _rate_limit()
    if blocked:
        return blocked

    data = request.get_json(silent=True) or {}
    question, bad = _validate(data)
    if bad:
        return bad

    # 闸门在这里手动拿、在生成器 finally 里放。
    # 不能用 with：视图函数返回时生成器还没开始跑，出了 with 就释放了。
    if not _ask_slots.acquire(blocking=False):
        METRICS.inc("concurrency_rejected_total")
        return _err("当前提问的人有点多，请稍后再试", 503)

    sess = store.get(_sid())
    rid = getattr(g, "rid", "")
    sid = sess.sid

    def generate():
        t0 = time.time()
        first_byte_at = None
        answer = ""
        try:
            # 同一会话串行；流式期间一直持锁，避免两条流交叉改 messages
            with sess.lock:
                for event, payload in sess.agent.ask_stream(question):
                    if event == "done":
                        answer = payload
                    if event == "delta" and first_byte_at is None:
                        first_byte_at = time.time()
                    if event == "hits":
                        yield _sse("hits", {"hits": payload})
                    elif event == "delta":
                        yield _sse("delta", {"text": payload})
                    elif event == "status":
                        yield _sse("status", {"text": payload})
                    elif event == "reset":
                        yield _sse("reset", {})
                    elif event == "done":
                        yield _sse("done", {"answer": payload,
                                            "request_id": rid})
                    elif event == "error":
                        yield _sse("error", {"error": payload})
                sess.turns += 1

            cost = time.time() - t0
            METRICS.inc("asks_total")
            METRICS.observe_ask(cost)
            if answer.startswith("抱歉，这次没能答上来"):
                METRICS.inc("ask_errors_total")
            obs.log_event(log, "ask_stream_done", session=sid, turns=sess.turns,
                          cost_ms=int(cost * 1000),
                          ttfb_ms=int((first_byte_at - t0) * 1000) if first_byte_at else None,
                          question_len=len(question), rid=rid)
        except Exception:
            log.exception("stream failed rid=%s", rid)
            METRICS.inc("ask_errors_total")
            yield _sse("error", {"error": "服务暂时不可用，请稍后再试"})
        finally:
            # 客户端断开也要释放，否则闸门会被慢慢占满
            _ask_slots.release()

    resp = Response(generate(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    # nginx 默认会缓冲响应，缓冲了就不叫流式了
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/clear", methods=["POST"])
def clear():
    """清空当前会话的记忆，从头开始。"""
    sid = _sid()
    memory.clear(sid)
    store.drop(sid)
    obs.log_event(log, "session_cleared", session=sid, rid=getattr(g, "rid", ""))
    return jsonify({"ok": True})


# ------------------------------------------------------------
# 健康检查：活着 / 能服务，是两件事
# ------------------------------------------------------------
@app.route("/livez")
def livez():
    """进程活着就 200。给容器用——它只该回答"要不要杀掉重启我"。"""
    return jsonify({"status": "alive",
                    "uptime_seconds": int(time.time() - STARTED_AT)})


@app.route("/readyz")
def readyz():
    """能不能真的对外服务：知识库有块才算 ready。

    不在这里探测外部 API（embedding / LLM）：探一次就要花钱，
    而且网络抖一下就把整批实例摘掉，被打死的比救活的多。
    外部依赖的状态放在 /health 的字段里给监控看。
    """
    blocks = len(knowledge.get_blocks())
    ready = blocks > 0
    body = {
        "status": "ready" if ready else "not_ready",
        "blocks": blocks,
        "retriever": config.RETRIEVER,
        "sessions": store.stats(),
    }
    return jsonify(body), (200 if ready else 503)


@app.route("/health")
def health():
    """综合状态。总返回 200 —— 缺 key 重启一百次也好不了，
    让它返回 503 会被平台反复重启。状态用字段表达。"""
    degraded = not (config.DEEPSEEK_KEY and config.DASHSCOPE_KEY)
    return jsonify({
        "status": "degraded" if degraded else "ok",
        "uptime_seconds": int(time.time() - STARTED_AT),
        "blocks": len(knowledge.get_blocks()),
        "retriever": config.RETRIEVER,
        "llm_configured": bool(config.DEEPSEEK_KEY),
        "embedding_configured": bool(config.DASHSCOPE_KEY),
        "sessions": store.stats(),
        "warnings": _report["warnings"],
    })


@app.route("/metrics")
def metrics():
    """Prometheus 文本格式。"""
    return Response(METRICS.prometheus(), mimetype="text/plain; version=0.0.4")


if __name__ == "__main__":
    print("=" * 60)
    print("  campus-rag-agent · Web")
    print("  http://127.0.0.1:%d" % config.PORT)
    print("=" * 60)
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)
