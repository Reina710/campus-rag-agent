# -*- coding: utf-8 -*-
"""
知识库工具：把检索包装成模型可自主调用的工具（function calling）。

之前是代码先检索、把资料塞进 system，模型被动接收；现在是模型先看到
工具清单，自己决定"该不该查、查什么词"。这个区别就是 RAG 问答升级成
RAG Agent 的分水岭。
"""

import retriever
import tools
import prompt as prompt_mod


@tools.agent_tool(
    name="search_knowledge",
    description="在知识库里检索资料。传一个 query（你要查的问题或关键词），"
                "返回最相关的几块资料。当你不确定答案、需要查资料时必须调用它。",
)
def search_knowledge(query: str) -> str:
    """查知识库：拿 query 去检索，把 top 几块的标题和正文拼成一段文字返回。"""
    global LAST_RAW_HITS
    blocks = _get_blocks()
    hits = retriever.search(query, blocks, _TOP_K)
    LAST_RAW_HITS = hits   # 记下原始结果，供评测统计命中用

    if not hits:
        return "（知识库里没检索到相关资料）"

    parts = []
    for i, h in enumerate(hits):
        b = h["block"]
        parts.append("【%d】%s\n%s" % (i + 1, b["title"], b["text"]))

    body = "\n\n".join(parts)
    # 防注入：把资料包进"不可信数据"信封再交给模型，让它知道这些是原材料而非命令
    return prompt_mod.wrap_untrusted(body)


@tools.agent_tool(
    name="list_topics",
    description="列出知识库里所有资料的标题（目录）。当用户问'你都知道些什么'、"
                "或需要先了解知识库覆盖范围时调用它。",
)
def list_topics() -> str:
    """列出知识库所有块的标题，让模型知道能查到的范围。"""
    blocks = _get_blocks()
    titles = ["%d. %s" % (b["id"] + 1, b["title"]) for b in blocks]
    return "知识库共 %d 块资料：\n%s" % (len(blocks), "\n".join(titles))


# ------------------------------------------------------------
# 内部：惰性加载知识库（只读一次，不重复切块）
# ------------------------------------------------------------
_BLOCKS = None
_TOP_K = 3
# 记录最近一次 search_knowledge 查到的原始结果（带分数），供评测模块算
# "检索命中"用。工具正常运行时不依赖它。
LAST_RAW_HITS = []


def _get_blocks():
    global _BLOCKS
    if _BLOCKS is None:
        import knowledge
        _BLOCKS = knowledge.load_blocks()
    return _BLOCKS


def set_top_k(k):
    """让外部能调 Top-K（评测对比用）。"""
    global _TOP_K
    _TOP_K = k


def last_raw_hits():
    """返回最近一次检索的原始 hits（每项是 {"block": ..., "score": ...}）。"""
    return LAST_RAW_HITS


if __name__ == "__main__":
    import json
    print(json.dumps(tools.build_tools_json(), ensure_ascii=False, indent=2))
    print("\n--- 实测调用 search_knowledge ---")
    print(search_knowledge("什么是 RAG"))
    print("\n--- 实测调用 list_topics ---")
    print(list_topics())
