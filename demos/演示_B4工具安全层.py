# -*- coding: utf-8 -*-
"""
B4 演示 —— 工具安全层能兜住哪些"会崩"的情况。

不真调模型，纯本地跑，把四类情况挨个试一遍：
    1. 工具不存在
    2. 参数 JSON 写坏了
    3. 参数类型对不上（少了必填）
    4. 工具自己执行崩了
    外加：side_effect 标记怎么区分只读/副作用工具。

跑法：python 演示_B4工具安全层.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import agent
import tools


def main():
    lines = []

    # 注册一个"会崩"的工具 + 一个"有副作用"的工具，用来演示
    @tools.agent_tool(name="demo_boom", description="演示用：一定会崩")
    def demo_boom(x: int) -> str:
        return str(1 / 0)   # 故意除以零

    @tools.agent_tool(name="demo_send", description="演示用：发消息", side_effect=True)
    def demo_send(to: str, text: str) -> str:
        return "已发给 %s" % to

    lines.append("=" * 66)
    lines.append("B4 工具安全层：四类崩溃 + 副作用标记")
    lines.append("=" * 66)

    lines.append("\n1. 工具不存在：")
    lines.append("   调用 'no_such_tool' → " + agent._execute_tool("no_such_tool", "{}"))

    lines.append("\n2. 参数 JSON 写坏了（模型常犯）：")
    lines.append("   参数 = '{这不是合法json' → " + agent._execute_tool("demo_boom", "{这不是合法json"))

    lines.append("\n3. 参数类型对不上（少了必填参数 x）：")
    lines.append("   参数 = '{}' → " + agent._execute_tool("demo_boom", "{}"))

    lines.append("\n4. 工具自己执行崩了（除以零）：")
    lines.append("   参数 = '{\"x\": 1}' → " + agent._execute_tool("demo_boom", "{\"x\": 1}"))

    lines.append("\n5. side_effect 标记：")
    lines.append("   demo_boom 只读？ %s" % agent._is_readonly("demo_boom"))
    lines.append("   demo_send 只读？ %s" % agent._is_readonly("demo_send"))
    lines.append("   正常调 demo_send → " + agent._execute_tool("demo_send", "{\"to\": \"张三\", \"text\": \"你好\"}"))

    lines.append("\n" + "=" * 66)
    lines.append("结论：不管模型怎么瞎搞，工具层都不崩，而是回一句它能看懂的错。")
    lines.append("      这就是你之前 5 个'静默失败'案例的'制度化'——不再沉默，而是报出来。")
    lines.append("=" * 66)

    with open("_b4_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("DONE")


if __name__ == "__main__":
    main()
