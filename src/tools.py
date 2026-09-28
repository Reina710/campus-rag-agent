# -*- coding: utf-8 -*-
"""
工具注册表：把普通函数注册成模型可调用的工具（OpenAI function calling）。

一个函数加一个 @agent_tool 装饰器，就会自动生成对应的 tools JSON：
    name         <- 装饰器里指定（不指定则用函数名）
    description  <- 装饰器里写的说明，原文传给模型
    parameters   <- 从函数参数名 + 类型标注自动推导
"""


# 全局登记表：所有注册过的工具都进这里
TOOL_REGISTRY = {}


def agent_tool(name, description, side_effect=False):
    """
    装饰器：把一个函数注册成工具。

    用法：
        @agent_tool(name="search_knowledge", description="查知识库")
        def search_knowledge(query: str) -> str:
            ...

    description 是给模型看的说明，模型靠它判断"现在该不该调这个工具"。
    说明写不好，模型就不知道工具是干什么的。
    """
    def decorator(func):
        TOOL_REGISTRY[name] = {
            "name": name,
            "description": description,
            "func": func,
            "side_effect": side_effect,   # True = 有副作用（发消息/改状态），需串行执行
        }
        return func
    return decorator


# ------------------------------------------------------------
# 参数类型标注 -> JSON schema 类型 的映射
# ------------------------------------------------------------
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _param_type(annotation):
    """把 Python 类型标注翻译成 JSON schema 的类型名。"""
    if annotation in _TYPE_MAP:
        return _TYPE_MAP[annotation]
    return "string"   # 认不出来的类型一律当字符串


def build_tools_json():
    """
    把所有已注册工具翻译成 OpenAI 的 tools 列表（供 function calling 用）。
    用 inspect 读函数签名，自动推导每个参数的类型和是否必填。
    """
    import inspect

    tools = []
    for name, entry in TOOL_REGISTRY.items():
        func = entry["func"]

        sig = inspect.signature(func)
        properties = {}
        required = []
        for pname, p in sig.parameters.items():
            if pname == "self":
                continue
            properties[pname] = {"type": _param_type(p.annotation)}
            if p.default is inspect.Parameter.empty:
                required.append(pname)

        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": entry["description"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return tools


def get_tool(name):
    """按名字取出一个工具（执行时用）。"""
    return TOOL_REGISTRY.get(name)


if __name__ == "__main__":
    # 直接运行本文件，可查看自动生成的 tools JSON 长什么样
    import json

    @agent_tool(name="demo_search", description="查询资料")
    def demo_search(query: str) -> str:
        return ""

    @agent_tool(name="demo_send", description="发消息", side_effect=True)
    def demo_send(to: str, text: str) -> str:
        return ""

    print(json.dumps(build_tools_json(), ensure_ascii=False, indent=2))
