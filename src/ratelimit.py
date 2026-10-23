# -*- coding: utf-8 -*-
"""
按 IP 的简单限流（滑动窗口）。

为什么要有：每问一句都要调模型，都是钱。上线后被人脚本刷一晚，
账单会很难看；也会把上游的配额打满，正常用户反而用不了。

为什么不用 Redis：单机部署，进程内足够。真实多实例部署时这一层要换成
Redis 或网关限流，否则 N 个实例就是 N 倍额度。
"""

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit, window_seconds=60):
        self.limit = limit
        self.window = window_seconds
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key):
        """放行返回 (True, 0)；超限返回 (False, 还需等几秒)。"""
        now = time.time()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                q = deque()
                self._hits[key] = q
            # 丢掉窗口外的记录
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                retry_after = int(self.window - (now - q[0])) + 1
                return False, retry_after
            q.append(now)
            # 防御：被大量不同 IP 扫时，顺手清理空队列，避免字典无限增长
            if len(self._hits) > 10000:
                for k in [k for k, v in self._hits.items() if not v][:5000]:
                    self._hits.pop(k, None)
            return True, 0
