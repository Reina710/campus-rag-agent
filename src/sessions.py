# -*- coding: utf-8 -*-
"""
会话管理：把一个进程变成"能同时服务多个人"。

为什么需要这一层：
    KBAgent 内部有两样东西是"每个用户一份"的状态——messages（对话历史）
    和 last_hits（上一轮命中）。整个进程共用一个实例，两个人同时提问就会
    串话：A 的问题会接在 B 的上下文后面，刷新页面还能看到对方的聊天记录。

设计取舍：
    - 会话存进程内存，不用 Redis。单机部署够用，代价是不支持多进程 worker，
      所以启动方式选"单进程 + 多线程"（见 Dockerfile 里的 gunicorn 参数）。
    - 有 TTL 和数量上限。访客不会主动"退出"，不回收就是内存泄漏。
    - 每个会话一把锁：同一个人连点两次发送，两个请求会同时改 messages。
"""

import threading
import time

import config
from agent import KBAgent


class _Session:
    """一个会话：一个 agent + 一把锁 + 最后一次活跃时间。"""

    def __init__(self, sid):
        self.sid = sid
        self.agent = KBAgent(sid)
        self.agent.load_history()
        self.lock = threading.Lock()
        self.last_seen = time.time()
        self.turns = 0


class SessionStore:
    def __init__(self, ttl=None, max_sessions=None):
        self.ttl = ttl if ttl is not None else config.SESSION_TTL_SECONDS
        self.max_sessions = max_sessions or config.MAX_SESSIONS
        self._items = {}
        self._lock = threading.Lock()
        self.created = 0
        self.evicted = 0

    def get(self, sid):
        """取会话，没有就建。返回的对象自带 lock，调用方负责加锁。"""
        if not sid:
            sid = "anonymous"
        now = time.time()
        with self._lock:
            self._evict(now)
            s = self._items.get(sid)
            if s is None:
                s = _Session(sid)
                self._items[sid] = s
                self.created += 1
            s.last_seen = now
            return s

    def _evict(self, now):
        """先按 TTL 清，再按数量上限淘汰最久没动的。调用方已持锁。"""
        dead = [k for k, v in self._items.items()
                if now - v.last_seen > self.ttl]
        for k in dead:
            self._items.pop(k, None)
        self.evicted += len(dead)

        while len(self._items) > self.max_sessions:
            oldest = min(self._items.items(), key=lambda kv: kv[1].last_seen)[0]
            self._items.pop(oldest, None)
            self.evicted += 1

    def drop(self, sid):
        with self._lock:
            return self._items.pop(sid, None) is not None

    def stats(self):
        with self._lock:
            # 只算还没过期的：淘汰是惰性的（下次 get 才清），
            # 直接把字典大小报出去会偏高，监控看到的是假数字。
            now = time.time()
            active = sum(1 for v in self._items.values()
                         if now - v.last_seen <= self.ttl)
            return {
                "active": active,
                "created": self.created,
                "evicted": self.evicted,
                "ttl_seconds": self.ttl,
                "max_sessions": self.max_sessions,
            }
