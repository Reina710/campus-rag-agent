# -*- coding: utf-8 -*-
"""
B3 演示 —— 对比"硬砍"和"压缩"，看谁把关键信息弄丢了。

场景：用户在最开始说过一句关键信息"我是研二专硕，想找算法实习"，
     后面又聊了很多无关的水话，把上下文撑爆。
     然后问"你还记得我是什么情况吗？"

    硬砍版：最老的那句关键信息被删了 → 模型答不上来。
    压缩版：关键信息被提炼进摘要保留了 → 模型答得上。

跑法：python 演示_B3压缩对照.py
注意：会调 DeepSeek，约 2 次调用。
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config
import compressor
import token_estimator
from openai import OpenAI

client = OpenAI(api_key=config.DEEPSEEK_KEY, base_url=config.DEEPSEEK_BASE_URL)


def ask_final(system_content, question):
    resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": question},
        ],
    )
    return resp.choices[0].message.content


def main():
    # 关键信息（最老的一条）
    key_info = "用户自我介绍：我是研二，专硕，想找算法方向的实习。"
    # 大量水话（撑爆上下文用）
    filler = "用户又问了一堆无关的问题，比如今天食堂有什么菜、图书馆几点关门。" * 40

    # 完整历史
    history = [
        {"role": "user", "content": key_info},
        {"role": "assistant", "content": "好的，我记住了你的情况。"},
        {"role": "user", "content": filler},
        {"role": "assistant", "content": "（聊了很多无关内容）"},
    ]

    question = "你还记得我是什么情况吗？（我的年级、类型、想找什么方向的实习）"

    lines = []
    lines.append("=" * 66)
    lines.append("关键信息（最老一条）：" + key_info)
    lines.append("历史总 token：%d" % token_estimator.estimate_messages(history))
    lines.append("=" * 66)

    # 硬砍版：直接删最老的（key_info 没了）
    hard_cut = history[2:]   # 砍掉前两条 = 关键信息 + 确认
    lines.append("\n【硬砍版】把最老的两条删了，关键信息没了：")
    lines.append("  → " + ask_final("你是助手。", question))

    # 压缩版：把历史压成摘要
    summary = compressor.compress_history(history)
    lines.append("\n【压缩版】历史被压成一段摘要：")
    lines.append("  摘要 = " + summary)
    lines.append("  → " + ask_final("（之前的对话摘要）" + summary, question))

    lines.append("\n对照看：硬砍版答不上关键信息，压缩版答得上 —— 这就是压缩的价值。")

    with open("_b3_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("DONE")


if __name__ == "__main__":
    main()
