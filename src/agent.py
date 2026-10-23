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
import logging
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


log = logging.getLogger("kb.agent")


# ------------------------------------------------------------
# 模型客户端：惰性创建
# ------------------------------------------------------------
# 为什么不写在模块顶层：健康检查必须能在"没配 key"的情况下也跑起来。
# 顶层直接建 client 的话，缺 key 会让整个模块 import 失败，服务连启动都起不来。
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.DEEPSEEK_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
            timeout=config.LLM_TIMEOUT,        # 单次请求超时（秒）
            max_retries=config.LLM_MAX_RETRIES,  # SDK 内置重试，只重试 5xx/超时
        )
    return _client


def _call_llm(messages, tool_choice="auto"):
    """
    问一次模型。tool_choice 决定让不让它调工具：
        "auto" = 让它自己判断（Agent 模式用）
        None    = 纯问答，不给工具（RAG 模式用）

    超时和重试交给 OpenAI SDK：timeout=N 控制单次等待上限，
    max_retries=N 在超时/5xx 时按指数退避重试；4xx（key 错、参数错）
    不重试，重试也没用。
    """
    kwargs = {"model": config.CHAT_MODEL, "messages": messages}
    if tool_choice == "auto":
        kwargs["tools"] = tools.build_tools_json()
        kwargs["tool_choice"] = "auto"
    return _get_client().chat.completions.create(**kwargs)


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


# 知识块是只读的，所有会话共用同一份（缓存在 knowledge.get_blocks 里）。
class KBAgent:
    def __init__(self, session_id="default"):
        # session_id 决定"这个 agent 的对话存到哪个文件"，也是隔离的最小单位
        self.session_id = session_id
        self.blocks = knowledge.get_blocks()
        self.messages = [{"role": "system", "content": ""}]
        self.last_hits = []          # 上一轮捞到了什么（给 /hits 看）

    # ------------------------------------------------------------
    # 持久化记忆：读回历史 / 存历史
    # ------------------------------------------------------------
    def load_history(self):
        """启动时把本会话上次存的历史读回来，接到 system 后面。"""
        if not config.MEMORY_ENABLED:
            return
        history = memory.load(self.session_id)
        if history:
            self.messages = [self.messages[0]] + history
            # 从磁盘读回的可能是旧版本存下的不完整序列，先洗一遍
            self._repair_tool_pairs()

    def save_history(self):
        """退出时把整段对话历史存进本会话自己的文件（system 那条除外）。"""
        if not config.MEMORY_ENABLED:
            return
        memory.save(self.messages, self.session_id)

    # ------------------------------------------------------------
    # 内部动作
    # ------------------------------------------------------------
    def retrieve(self, question):
        """拿问题去知识库里找资料（rag 模式用）。"""
        return retriever.search(question, self.blocks, config.TOP_K)

    # ------------------------------------------------------------
    # 裁剪的边界保护
    # ------------------------------------------------------------
    def _turn_starts(self):
        """每条 user 消息的下标 = 一个完整对话轮次的起点。

        裁剪必须以"轮次"为单位。按单条消息删会把
        assistant(tool_calls) 和它后面的 tool 拆开，
        而接口硬性要求 role=tool 必须紧跟带 tool_calls 的 assistant，
        拆开后每次请求都 400，且坏状态只在内存里，磁盘上看不出来。
        """
        return [i for i, m in enumerate(self.messages) if m.get("role") == "user"]

    def _repair_tool_pairs(self):
        """丢掉没有对应 assistant(tool_calls) 的 tool 消息。

        这是最后一道防线：无论历史是怎么坏的（裁剪、从磁盘读回、
        中途异常回滚），只要在发请求前过一遍，就不会把非法序列送出去。
        """
        fixed = []
        pending = set()          # 上一条 assistant 声明过的 tool_call id
        dropped = 0
        for m in self.messages:
            r = m.get("role")
            if r == "assistant" and m.get("tool_calls"):
                pending = {tc.get("id") for tc in m["tool_calls"]}
                fixed.append(m)
            elif r == "tool":
                if m.get("tool_call_id") in pending:
                    fixed.append(m)
                    pending.discard(m.get("tool_call_id"))
                else:
                    dropped += 1
            else:
                pending = set()
                fixed.append(m)
        if dropped:
            log.warning("丢弃 %d 条孤立的 tool 消息（历史被裁剪拆散了），session=%s",
                        dropped, self.session_id)
            self.messages = fixed
        return dropped

    def trim(self):
        """
        按 token 预算裁剪：算出 messages 总 token，超过预算就从最老的整轮开始删。

        为什么按"轮次"删而不是按条删：
            一轮问答在 Agent 模式下会展开成
                user -> assistant(tool_calls) -> tool -> assistant(answer)
            四条消息。逐条从头删，很可能正好删掉 assistant(tool_calls)
            却留下 tool，接口直接拒绝（role=tool 找不到前置 tool_calls）。
            按轮次删，切点永远落在 user 前面，配对天然完整。
        """
        budget = config.CONTEXT_MAX_TOKENS
        # 至少留 system + 最后一轮，别删光
        while len(self.messages) > 2 and \
                token_estimator.estimate_messages(self.messages) > budget:
            starts = self._turn_starts()
            if len(starts) <= 1:
                break                    # 只剩最后一轮，再删就答不了
            del self.messages[1:starts[1]]   # 删掉最早一整轮（system 除外）

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

        # 切点必须落在"轮次起点"上。
        # 原来用 messages[-2:] 取最新两条，如果倒数第二条正好是 tool，
        # 切完就留下一条没有 tool_calls 的 tool 消息 —— 整段历史作废。
        starts = self._turn_starts()
        if len(starts) < 2:
            self.trim()
            return

        cut = starts[-1]                  # 最后一轮的起点
        old = self.messages[1:cut]        # 中间的老历史
        recent = self.messages[cut:]      # 最新一轮（含它的全部 tool 往返）

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
        """对外唯一出口。任何异常都在这里收口，不往上抛。

        为什么必须收口：Web 服务里一个没接住的异常就是 500，用户看到一片白。
        模型超时、key 过期、网络抖动，这些的正确答案是"这句话没答上来"，
        不是"整个服务坏了"。

        出错时把这轮加进去的消息删掉：否则下一轮会带着半截对话继续，
        模型看到"用户问了但助手没答"会自己编一个回答补上。
        """
        before = len(self.messages)
        try:
            if config.AGENT_MODE == "tool":
                answer = self._ask_tool_mode(question)
            else:
                answer = self._ask_rag_mode(question)
        except Exception:
            del self.messages[before:]
            # 坏消息可能在本轮之前就已经存在（比如裁剪留下的孤立 tool），
            # 只回滚本轮删不掉它，下一轮还会同样 400。
            self._repair_tool_pairs()
            log.exception("ask failed, session=%s question=%.40s",
                          self.session_id, question)
            return "抱歉，这次没能答上来（服务暂时不可用），请稍后再试一次。"

        # 每轮结束就落盘。Web 服务没有"退出"这个时机，
        # 只在退出时保存的话，用户刷新页面历史就没了。
        try:
            self.save_history()
        except OSError:
            log.warning("save history failed, session=%s", self.session_id)
        return answer

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
            # 发请求前最后过一遍：宁可丢掉一条孤立的 tool 消息，也不要整轮 400
            self._repair_tool_pairs()
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
