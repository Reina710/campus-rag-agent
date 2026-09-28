# -*- coding: utf-8 -*-
"""
Agent 主循环，有两种模式，区别在"谁来决定检索"：

模式一（rag）：代码先检索，把资料塞进 system，模型被动接收（RAG 问答）。
模式二（tool）：把检索包成工具，模型自己决定查不查、查什么（真 Agent，
              模型有决策权，能连续调几次工具）。

模式二的循环是：
    while 未到上限:
        问模型
        if 模型没要求调工具: 返回答案
        否则: 执行它要的工具 -> 把结果回填 -> 再问一轮
"""

import json
import config
import knowledge
import prompt as prompt_mod
import retriever
import tools
import kb_tools   # 导入它，两个工具才会注册进登记表
import token_estimator   # 按 token 裁剪用
import compressor   # 上下文压缩用
import memory   # 持久化记忆用
from openai import OpenAI


client = OpenAI(api_key=config.DEEPSEEK_KEY, base_url=config.DEEPSEEK_BASE_URL)


def _call_llm(messages, tool_choice="auto"):
    """
    问一次模型。tool_choice 决定让不让它调工具：
        "auto" = 让它自己判断（Agent 模式用）
        None    = 纯问答，不给工具（RAG 模式用）
    """
    kwargs = {"model": config.CHAT_MODEL, "messages": messages}
    if tool_choice == "auto":
        kwargs["tools"] = tools.build_tools_json()
        kwargs["tool_choice"] = "auto"
    return client.chat.completions.create(**kwargs)


def _execute_tool(name, arguments):
    """
    执行一个工具，返回结果字符串。兜住三类"会崩"的情况，不把异常抛出去：
        1. 工具名不存在 -> 返回一句可读的提示
        2. 参数 JSON 写坏 -> 返回格式错误提示，让模型重发
        3. 工具执行时抛异常 -> 返回异常类型，不把堆栈丢给模型

    另外，声明了 side_effect=True 的工具（如发消息）结果前会加标记，
    供上层区分"只读工具可并行，有副作用的必须串行"。
    """
    entry = tools.get_tool(name)
    if entry is None:
        return "[工具不存在：%s]" % name

    # 参数兜底：模型给的 arguments 可能是字符串 JSON，也可能是 dict
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        return "[工具参数格式错误，请重新生成]"

    try:
        result = entry["func"](**args)
        prefix = "[已执行副作用工具]" if entry.get("side_effect") else ""
        return prefix + str(result)
    except TypeError as e:
        # 参数对不上（少必填、类型不对），这是模型传参错误
        return "[工具参数错误：%s，请检查参数后重试]" % e
    except Exception as e:
        # 工具内部真出错，报异常类型，不把堆栈丢给模型
        return "[工具执行失败：%s]" % type(e).__name__


def _is_readonly(name):
    """判断工具是否只读（无副作用）。只读的可以放心并行/重试。"""
    entry = tools.get_tool(name)
    if entry is None:
        return True   # 不存在的工具当只读处理（反正会报错，不会有副作用）
    return not entry.get("side_effect", False)


def _think(text):
    """把 Agent 的中间动作打印出来，让用户看到它在想什么、调了什么工具。
    开关在 config.SHOW_THINKING（评测时关闭，保持输出干净）。"""
    if config.SHOW_THINKING:
        print("  [agent] %s" % text, flush=True)


class KBAgent:
    def __init__(self):
        self.blocks = knowledge.load_blocks()
        self.messages = [{"role": "system", "content": ""}]
        self.last_hits = []          # 上一轮捞到了什么（给 /hits 看）

    # ------------------------------------------------------------
    # 持久化记忆：读回历史 / 存历史
    # ------------------------------------------------------------
    def load_history(self):
        """启动时把上次存的历史读回来，接到 system 后面。"""
        if not config.MEMORY_ENABLED:
            return
        history = memory.load()
        if history:
            self.messages = [self.messages[0]] + history

    def save_history(self):
        """退出时把整段对话历史存进文件（system 那条除外）。"""
        if not config.MEMORY_ENABLED:
            return
        memory.save(self.messages)

    # ------------------------------------------------------------
    # 内部动作
    # ------------------------------------------------------------
    def retrieve(self, question):
        """拿问题去知识库里找资料（rag 模式用）。"""
        return retriever.search(question, self.blocks, config.TOP_K)

    def trim(self):
        """
        按 token 预算裁剪：算出 messages 总 token，超过预算就从头删最老的
        （跳过 system），直到回到预算以内。比"数条数"更准——一条 3000 字的
        消息顶得上几十条短消息，光数条数会严重低估实际长度。
        """
        budget = config.CONTEXT_MAX_TOKENS
        # 至少保留 system + 最新一条，别删光
        while len(self.messages) > 2 and \
                token_estimator.estimate_messages(self.messages) > budget:
            del self.messages[1]

    def trim_smart(self):
        """
        智能裁剪：先压缩，压缩不够再硬砍。

        流程：
            1. 总 token 没超预算，直接返回。
            2. 超了，把中间的老历史（system 之后、最新几轮之前）交给模型压成摘要。
            3. 用摘要替换老历史。
            4. 压缩后仍超预算，才退回 trim() 硬砍。

        注意：压缩会多调一次模型（花钱），只在真的超了时才做。
        """
        budget = config.CONTEXT_MAX_TOKENS
        if token_estimator.estimate_messages(self.messages) <= budget:
            return   # 没超，什么都不做

        if len(self.messages) <= 3:
            self.trim()
            return

        old = self.messages[1:-2]     # 中间的老历史
        recent = self.messages[-2:]   # 最新一轮，保留原样

        summary = compressor.compress_history(old)
        if summary.strip():
            # 用摘要替换老历史：system + 摘要 + 最新一轮
            self.messages = [self.messages[0]] + \
                [{"role": "system", "content": "（之前的对话摘要）" + summary}] + \
                recent
        # 压缩后如果还超，退回硬砍
        if token_estimator.estimate_messages(self.messages) > budget:
            self.trim()

    # ------------------------------------------------------------
    # 对外：问一个问题，拿一个回答
    # ------------------------------------------------------------
    def ask(self, question):
        if config.AGENT_MODE == "tool":
            return self._ask_tool_mode(question)
        return self._ask_rag_mode(question)

    # ------------------------------------------------------------
    # 模式一：rag（代码先检索）
    # ------------------------------------------------------------
    def _ask_rag_mode(self, question):
        hits = self.retrieve(question)
        self.last_hits = hits

        blocks = [h["block"] for h in hits]
        self.messages[0] = {
            "role": "system",
            "content": prompt_mod.build_system(blocks),
        }

        self.messages.append({"role": "user", "content": question})
        self.trim_smart()

        resp = _call_llm(self.messages, tool_choice=None)
        answer = resp.choices[0].message.content
        self.messages.append({"role": "assistant", "content": answer})
        return answer

    # ------------------------------------------------------------
    # 模式二：tool（Agent，模型自主决定调不调工具）
    # ------------------------------------------------------------
    def _ask_tool_mode(self, question):
        # system 固定一句话，不再每轮塞资料，资料改由模型自己查
        self.messages[0] = {"role": "system", "content": prompt_mod.get_system_agent()}

        self.messages.append({"role": "user", "content": question})

        for _ in range(config.MAX_LOOPS):
            resp = _call_llm(self.messages, tool_choice="auto")
            msg = resp.choices[0].message

            # 模型没要求调工具 -> 这就是最终答案
            if not msg.tool_calls:
                answer = msg.content or ""
                self.messages.append({"role": "assistant", "content": answer})
                self.trim_smart()
                return answer

            # 模型调工具前"开口说的那句话" = 它的思考，亮出来
            if msg.content and msg.content.strip():
                _think("思考：%s" % msg.content.strip())

            # 模型要调工具 -> 记下动作，然后执行
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
            self.messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": tool_calls,
            })

            # 逐个执行它要的工具，把结果回填（role="tool" + tool_call_id）
            for tc in msg.tool_calls:
                _think("调用工具 %s(%s)" % (tc.function.name, tc.function.arguments))
                result = _execute_tool(tc.function.name, tc.function.arguments)
                preview = result.replace("\n", " ")[:80]
                _think("工具返回：%s%s" % (preview, "…" if len(result) > 80 else ""))
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
                # 记录这次检索拿到的资料，供评测模块算"检索命中"。
                # 只有 search_knowledge 会真的去查资料，所以只记它。
                if tc.function.name == "search_knowledge":
                    self.last_hits = kb_tools.last_raw_hits()

        # 循环到上限还没给最终答案 -> 逼它收口
        self.messages.append({
            "role": "user",
            "content": "[已达到最大工具调用次数，请基于已有信息直接回答。]",
        })
        resp = _call_llm(self.messages, tool_choice=None)
        answer = resp.choices[0].message.content
        self.messages.append({"role": "assistant", "content": answer})
        self.trim_smart()
        return answer
