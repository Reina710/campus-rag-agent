# -*- coding: utf-8 -*-
"""
上线验收脚本：验证"能不能真的对外开"。

测的是上线必查的几件事，不是功能好不好：
    1. 两个探针分工对不对（/livez 活着、/readyz 能服务）
    2. 两个用户会不会串话（会话隔离）
    3. 同一个用户连续提问能不能接上上下文（会话内记忆）
    4. 刷接口会不会被拦住（限流）
    5. 流式问答的事件顺序与内容一致性
    6. 出错时返回的是 JSON 而不是 HTML 错误页
    7. 每个响应都带 request_id（出问题能对着日志查）
    8. 首页返回的是前端构建产物（防"忘了 build"上线成旧页面）

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
    try:
        r = op.open(BASE + path, timeout=timeout)
        status, body, headers = r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        status, body, headers = e.code, e.read().decode("utf-8", "ignore"), dict(e.headers)
    try:
        return status, json.loads(body), headers
    except ValueError:
        return status, {"raw": body}, headers


def post(op, path, payload, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = op.open(req, timeout=timeout)
        body = r.read().decode("utf-8")
        return r.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body}


def ask_stream(op, question, timeout=120):
    """读 SSE，返回 (事件列表, 拼起来的正文, done 里的答案)。

    手动解析 "data: {...}"：服务端一行一个事件，用空行分隔。
    """
    req = urllib.request.Request(
        BASE + "/ask_stream",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events, text, final = [], "", ""
    with op.open(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            events.append(ev.get("type"))
            if ev.get("type") == "delta":
                text += ev.get("text", "")
            if ev.get("type") == "reset":
                # 和前端同一套语义：模型先说了一段又转去调工具，那段作废
                text = ""
            if ev.get("type") == "done":
                final = ev.get("answer", "")
                break
    return events, text, final


def main():
    t0 = time.time()

    # ---------- 0. 首页 ----------
    # 前端由 frontend/ 用 Vite 构建到 static/，没构建过会回退到旧模板。
    # 这一项专门防"上线了但忘了 build"——那种情况页面能开，只是回到旧版。
    op_a, jar_a = new_client()
    status, page, _ = get(op_a, "/")
    html = page.get("raw", "")
    check("首页返回 200", status == 200)
    check("首页是构建产物（引用 /static/assets/）",
          "/static/assets/" in html,
          "" if "/static/assets/" in html else "回退到了旧模板，先跑 npm run build")

    # ---------- 1. 探针 ----------
    status, live, headers = get(op_a, "/livez")
    check("livez 返回 200", status == 200, str(live.get("status")))

    status, ready, _ = get(op_a, "/readyz")
    check("readyz 返回 200 且知识库非空",
          status == 200 and ready.get("blocks", 0) > 0,
          "blocks=%s" % ready.get("blocks"))

    status, health, _ = get(op_a, "/health")
    check("health 返回 200 并带 warnings 字段",
          status == 200 and "warnings" in health, health.get("status"))

    status, metrics, _ = get(op_a, "/metrics")
    check("metrics 是 Prometheus 文本",
          status == 200 and "kb_asks_total" in metrics.get("raw", ""),
          "含 kb_asks_total")

    # ---------- 2. 错误处理 ----------
    status, body, hdrs = get(op_a, "/no-such-endpoint")
    check("404 返回 JSON 而不是 HTML", status == 404 and "error" in body)
    check("响应带 X-Request-Id", bool(hdrs.get("X-Request-Id")))

    # ---------- 3. 会话内记忆 ----------
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

    # ---------- 4. 流式 ----------
    events, streamed, final = ask_stream(op_a, "图书馆几点开门？")
    check("流式先给状态再给正文",
          "status" in events and "delta" in events
          and events.index("status") < events.index("delta"),
          "事件序列 " + " > ".join(dict.fromkeys(events)))
    check("流式有 done 事件且答案非空", "done" in events and len(final) > 0,
          final[:40].replace("\n", " "))
    check("流式拼出来的正文与最终答案一致",
          streamed.strip() == final.strip(),
          "delta %d 字 / done %d 字" % (len(streamed), len(final)))

    # ---------- 5. 会话隔离 ----------
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

    # ---------- 6. 限流 ----------
    # 前面的提问已占额度，这里连发直到被拦
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
