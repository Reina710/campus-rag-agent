# -*- coding: utf-8 -*-
"""
B2 演示 —— 看"按条数裁"和"按 token 裁"到底差在哪。

关键点：一条超长消息，能骗过"数条数"，但骗不过"按 token 算"。

跑法：python 演示_B2按token裁.py
不花钱，纯本地算。
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config
import token_estimator


def main():
    # 造一组消息：system + 一轮超长对话 + 一轮正常对话
    long_text = "这是一条超长消息。" * 200   # 约 2000 字
    messages = [
        {"role": "system", "content": "你是助手。"},
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": "收到。"},
        {"role": "user", "content": "现在问个新问题"},
    ]

    lines = []
    lines.append("=" * 66)
    lines.append("消息一共 %d 条" % len(messages))
    lines.append("按条数看：%d 条，看似很短" % len(messages))
    total = token_estimator.estimate_messages(messages)
    lines.append("按 token 看：%d tokens（预算 %d）" % (total, config.CONTEXT_MAX_TOKENS))
    lines.append("")
    lines.append("那条超长消息本身：%d tokens" % token_estimator.estimate_tokens(long_text))
    lines.append("")
    lines.append("结论：数条数只看到 4 条，觉得没问题；")
    lines.append("      按 token 一看，一条消息就快顶爆预算了。")
    lines.append("      这就是为什么 B2 要把裁剪改成按 token，而不是数条数。")
    lines.append("=" * 66)

    with open("_b2_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("DONE")


if __name__ == "__main__":
    main()
