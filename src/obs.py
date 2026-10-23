# -*- coding: utf-8 -*-
"""
可观测性：一行一个 JSON 的日志 + 进程内指标。

为什么单独抽一层：
    上线以后排查问题的第一个问题不是"代码哪行错了"，而是
    "哪个请求、多慢、是不是降级了、是不是被限流了"。
    这些东西如果只在脑子里有，出事时只能靠猜。

两件事：
    1. 结构化日志 —— 每条日志是一行 JSON，字段固定，能被 grep / jq / 日志平台直接过滤
    2. 指标 —— 计数器 + 延迟分位，`/metrics` 以 Prometheus 文本格式暴露

为什么不用 prometheus_client：
    多一个依赖只为了几个 counter 不划算，文本格式本身很简单。
    指标存进程内存，容器重启就清零 —— 监控系统按 counter 累加，不受影响。

注意：这里的指标是进程内的。开多个 worker 时每个进程各算各的，
要么只开一个 worker（本项目的选择），要么换成 pushgateway / 多实例聚合。
"""

import json
import logging
import threading
import time
from collections import defaultdict, deque

STARTED_AT = time.time()

# 延迟样本窗口：只保留最近 N 次，算分位数用。
# 全量保存会无限涨，而监控想看的是"最近这段时间"的表现。
_WINDOW = 512


# ------------------------------------------------------------
# 日志
# ------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    """把日志打成一行 JSON。

    固定字段 + 自定义字段，好处是日志平台可以直接按字段建索引，
    不用写正则去啃自由文本。
    """

    def format(self, record):
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
                  + ".%03d" % (record.msecs),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level=logging.INFO, json_mode=True):
    """配置根 logger。json_mode=False 时保留人读的格式（本地开发用）。"""
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    if json_mode:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    # 第三方库的 INFO 太吵，压到 WARNING
    for noisy in ("urllib3", "openai", "httpx", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return root


def log_event(logger, event, msg="", level=logging.INFO, **fields):
    """打一条带 event 名和自定义字段的结构化日志。

    用法：log_event(log, "ask_done", question_len=12, cost_ms=830, session="abc")
    """
    logger.log(level, msg or event, extra={"event": event, "fields": fields})


# ------------------------------------------------------------
# 指标
# ------------------------------------------------------------
class Metrics:
    def __init__(self, window=_WINDOW):
        self._lock = threading.Lock()
        self.counters = defaultdict(int)      # 名字 -> 累计值
        self.lat_ask = deque(maxlen=window)   # 问答耗时（秒）
        self.lat_http = deque(maxlen=window)  # 请求耗时（秒）

    def inc(self, name, n=1):
        with self._lock:
            self.counters[name] += n

    def observe_ask(self, seconds):
        with self._lock:
            self.lat_ask.append(seconds)

    def observe_http(self, seconds):
        with self._lock:
            self.lat_http.append(seconds)

    @staticmethod
    def _pct(samples, p):
        """最近窗口内的分位值。样本为空返回 None，别返回 0 假装很快。"""
        if not samples:
            return None
        ordered = sorted(samples)
        # 最近秩法，p=0.95 时取第 95% 位置的样本
        idx = min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))
        return ordered[idx]

    def snapshot(self):
        with self._lock:
            counters = dict(self.counters)
            lat_ask = list(self.lat_ask)
            lat_http = list(self.lat_http)
        return {
            "uptime_seconds": int(time.time() - STARTED_AT),
            "counters": counters,
            "ask_latency": {
                "count": len(lat_ask),
                "p50": self._pct(lat_ask, 0.50),
                "p95": self._pct(lat_ask, 0.95),
                "max": max(lat_ask) if lat_ask else None,
            },
            "http_latency": {
                "count": len(lat_http),
                "p50": self._pct(lat_http, 0.50),
                "p95": self._pct(lat_http, 0.95),
            },
        }

    def prometheus(self):
        """Prometheus 文本格式。http_requests_total 按状态码拆成多条。

        状态码列表要固定（200/400/404/429/500/503），
        不然一次 4xx 会往序列里塞一条没人预期的新指标。
        """
        snap = self.snapshot()
        c = snap["counters"]
        lines = []

        def metric(name, mtype, help_text, value):
            lines.append("# HELP %s %s" % (name, help_text))
            lines.append("# TYPE %s %s" % (name, mtype))
            lines.append("%s %s" % (name, value))

        metric("kb_uptime_seconds", "gauge", "进程运行秒数", snap["uptime_seconds"])
        metric("kb_asks_total", "counter", "问答请求总数", c.get("asks_total", 0))
        metric("kb_ask_errors_total", "counter", "问答失败数（已兜底为用户可见提示）",
               c.get("ask_errors_total", 0))
        metric("kb_ask_degraded_total", "counter", "发生检索降级的问答数",
               c.get("ask_degraded_total", 0))
        metric("kb_ratelimit_hits_total", "counter", "被限流拒绝的请求数",
               c.get("ratelimit_hits_total", 0))
        metric("kb_concurrency_rejected_total", "counter", "并发满被拒绝的请求数",
               c.get("concurrency_rejected_total", 0))
        metric("kb_sessions_active", "gauge", "当前活跃会话数", c.get("sessions_active", 0))

        for code in (200, 400, 404, 405, 413, 429, 500, 503):
            lines.append('kb_http_requests_total{status="%d"} %d'
                         % (code, c.get("http_%d" % code, 0)))

        if snap["ask_latency"]["p50"] is not None:
            metric("kb_ask_latency_p50_seconds", "gauge", "问答耗时 P50",
                   round(snap["ask_latency"]["p50"], 3))
            metric("kb_ask_latency_p95_seconds", "gauge", "问答耗时 P95",
                   round(snap["ask_latency"]["p95"], 3))
        if snap["http_latency"]["p50"] is not None:
            metric("kb_http_latency_p50_seconds", "gauge", "请求耗时 P50",
                   round(snap["http_latency"]["p50"], 3))
            metric("kb_http_latency_p95_seconds", "gauge", "请求耗时 P95",
                   round(snap["http_latency"]["p95"], 3))
        return "\n".join(lines) + "\n"


# 进程级单例。多进程部署时每个进程一份，这是已知边界。
METRICS = Metrics()
