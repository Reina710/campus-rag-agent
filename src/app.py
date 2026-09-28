# -*- coding: utf-8 -*-
"""
项目入口：双击 run.bat 或运行 python app.py。

这个文件刻意写得很薄，只负责跟人交互，真正的逻辑都在 agent.py 里。
"""

import config
import memory
from agent import KBAgent


def show_hits(agent):
    """把上一轮捞到的资料打出来，调试时看它到底捞了什么。"""
    if not agent.last_hits:
        print("\n（还没问过问题）")
        return
    print("\n—— 上一轮捞到的资料 ——")
    for i, h in enumerate(agent.last_hits):
        print("   %d. [%.4f]  %s" % (i + 1, h["score"], h["block"]["title"]))
    print("—— 完 ——")


def show_history(agent):
    """把当前对话历史打出来（system 那条除外）。"""
    body = agent.messages[1:]
    if not body:
        print("\n（还没有对话历史）")
        return
    print("\n—— 对话历史 ——")
    for m in body:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            label = {"user": "你", "assistant": "助手"}.get(role, role)
            print("  [%s] %s" % (label, content.strip()[:60]))
    print("—— 完 ——")


def main():
    print("=" * 60)
    print("  校园信息问答 Agent")
    print("=" * 60)

    agent = KBAgent()

    # 启动时读回上次的历史（有的话）
    agent.load_history()
    if memory.has_history():
        print("（已恢复上次的对话，共 %d 条历史）" % (len(agent.messages) - 1))

    print("知识库：%d 块资料（切块方式：%s）" % (len(agent.blocks), config.CHUNK_MODE))
    print("检索器：%s      每次取前 %d 块" % (config.RETRIEVER, config.TOP_K))
    print("-" * 60)
    print("直接输入问题回车。")
    print("输入 q 退出；/hits 看上一轮捞的资料；/history 看历史；/clear 清空记忆。")
    print("-" * 60)

    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not question:
            continue

        if question.lower() in ("q", "quit", "exit"):
            agent.save_history()   # 退出前存历史
            print("已保存历史，再见。")
            break

        if question == "/hits":
            show_hits(agent)
            continue

        if question == "/history":
            show_history(agent)
            continue

        if question == "/clear":
            memory.clear()
            agent.messages = [agent.messages[0]]   # 只留 system
            print("\n已清空记忆。")
            continue

        try:
            answer = agent.ask(question)
        except Exception as e:
            print("\n[出错了] %s: %s" % (type(e).__name__, e))
            continue

        print("\n助手：%s" % answer)


if __name__ == "__main__":
    main()
