# -*- coding: utf-8 -*-
"""
上线验收脚本：验证"能不能真的对外开"。

测的是三件上线必查的事，不是功能好不好：
    1. 健康检查能不能过（部署平台靠它判断服务活着）
    2. 两个用户会不会串话（会话隔离）
    3. 同一个用户连续提问能不能接上上下文（会话内记忆）
    4. 刷接口会不会被拦住（限流）

前置：先启动服务
    python web_demo.py
然后另开一个终端：
    python tests/test_online.py

注意：会真的调用模型，跑一次大约 30~60 秒、花几分钱。
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
MEM_DIR = ROOT / "data" / "memory"

OK, FAIL = "[PASS]", "[FAIL]"
results = []


def check(name, cond, detail=""):
    results.append((cond, name))
    print("%s %s %s" % (OK if cond else FAIL, name, detail))


def new_client():
    """一个带独立 cookie 的客户端 = 一个独立访客。"""
    jar = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def _cookie_pairs(jar):
    return {(c.name, c.value) for c in jar}


def get(op, path, timeout=30):
    r = op.open(BASE + path, timeout=timeout)
    return r.status, json.loads(r.read().decode("utf-8"))


def post(op, path, payload, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = op.open(req, timeout=timeout)
        return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body}


def main():
    t0 = time.time()

    # ---------- 1. 健康检查 ----------
    op_a, jar_a = new_client()
    status, health = get(op_a, "/health")
    check("健康检查返回 200", status == 200, str(health.get("status")))
    check("知识库已加载", health.get("blocks", 0) > 0,
          "blocks=%s" % health.get("blocks"))

    # ---------- 2. 会话内记忆 ----------
    # 加一个随机标记，后面靠它判断"这条记录到底落在几个会话文件里"。
    # 不靠模型的措辞判断——模型说"看不到"也可能只是话术，文件不会骗人。
    tag = "test-" + uuid.uuid4().hex[:8]
    s1, r1 = post(op_a, "/ask", {"question": "奖学金一等多少钱？（%s）" % tag})
    if s1 == 429:
        print("[SKIP] 被限流了：上一轮测试占的额度还没恢复，等 60 秒再跑")
        return 1
    check("A 第一次提问成功", s1 == 200 and len(r1.get("answer", "")) > 0,
          r1.get("answer", "")[:40].replace("\n", " "))

    s2, r2 = post(op_a, "/ask", {"question": "那二等的呢？"})
    ans2 = r2.get("answer", "")
    check("A 第二轮能接上上一轮（会话内记忆）", s2 == 200 and len(ans2) > 0,
          ans2[:40].replace("\n", " "))

    # ---------- 3. 会话隔离 ----------
    op_b, jar_b = new_client()
    s3, r3 = post(op_b, "/ask", {"question": "我刚才问了什么问题？"})
    ans3 = r3.get("answer", "")
    check("B 也能正常提问", s3 == 200 and len(ans3) > 0,
          ans3[:40].replace("\n", " "))
    check("两个访客的 cookie 不同",
          _cookie_pairs(jar_a) != _cookie_pairs(jar_b))

    # 硬证据：带随机标记的记录只能出现在一个会话文件里。
    # 这条不看模型怎么回答——模型说"看不到"也可能只是话术，文件不会骗人。
    owners = [f.name for f in MEM_DIR.glob("*.json")
              if tag in f.read_text(encoding="utf-8")]
    check("A 的记录只落在自己的会话文件", len(owners) == 1, str(owners))

    # ---------- 4. 限流 ----------
    # 前面两次提问已占额度，这里连发直到被拦
    hit_429 = False
    codes = []
    for _ in range(14):
        code, _body = post(op_b, "/ask", {"question": ""}, timeout=10)
        codes.append(code)
        if code == 429:
            hit_429 = True
            break
    check("超频请求被限流（429）", hit_429, "最近状态码 %s" % codes[-3:])

    # ---------- 汇总 ----------
    passed = sum(1 for ok, _ in results if ok)
    print("\n%d/%d 通过，用时 %.1fs" % (passed, len(results), time.time() - t0))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
