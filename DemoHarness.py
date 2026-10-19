import sys
from pathlib import Path
from openai import OpenAI
import os
import json
import subprocess
sys.stdout.reconfigure(errors="replace")


MAX_CHARS_LIMIT = 200_000
env_file = Path(__file__).resolve().parent / '.env'
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",  # 换 Kimi/智谱/本地 Ollama 只改这一行
)


def read_file(path: str, max_chars: int = 8000) -> str:
    """读取文本文件的内容。当需要查看代码、配置、日志等文件时才用这个工具。"""
    p = Path(path)

    if not p.exists():
        return f"错误：文件不存在：{p}"
    if p.is_dir():
        return f"错误：这是一个目录，不是文件：{p}"
    # 模型读取文件操作
    try:
        # 定阅读字节的上下限
        max_chars = max(1, min(int(max_chars), MAX_CHARS_LIMIT))
        # 读取操作
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars + 1)
    except PermissionError:
        return f"错误：没有权限读取：{p}"
    except OSError as exc:
        return f"错误：读取失败：{type(exc).__name__}: {exc}"
    except Exception as exc:  # ← 兜底：留一道异常类型总防线
        return f"错误：读取失败：{type(exc).__name__}: {exc}"

    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]  # 先截断，后面所有判断都基于"真正要给模型看的内容"

    if "\x00" in content[:1024]:
        return f"错误：这看起来是二进制文件，不是文本：{p}"

    notes = []
    if content.count("\ufffd") > 100:
        notes.append("警告：该文件可能不是 UTF-8 编码（检测到大量无法解码的字节），以下内容可能不可靠")
    if truncated:
        notes.append(f"注：文件更长，只显示前 {max_chars} 个字符")

    prefix = "".join(n + "\n" for n in notes)
    return prefix + content


def run_command(command: str, timeout_seconds: int = 60) -> str:
    """执行一条命令行命令，返回退出码和输出。当需要运行程序、跑测试、看目录时才用这个工具。"""
    # TODO(以后): 输出太长要截断。实测 dir /s 一条就是 2.5 MB，够把上下文一拳打死。
    try:
        proc = subprocess.run(
            command,
            shell=True,          # 让 dir / 管道 / & 能用。代价：等于把整个 cmd.exe 交给模型 —— 这个洞是 B 阶段权限系统存在的理由
            capture_output=True,
            encoding="utf-8",    # 实测：这台机器的 cmd 输出是 UTF-8
            errors="replace",    # ← 不能省。省了它解码线程会死，p.stdout 变成 None，报错还会指向别处
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        # 实测 exc.stdout 是空的：超时前它已经打印的内容拿不到，别假装有
        return f"错误：命令超过 {timeout_seconds} 秒没有结束，已被强制终止。\n命令：{command}"
    except Exception as exc:
        # 工具永不抛异常：任何意外都翻译成一句话给模型看
        return f"错误：命令执行失败：{type(exc).__name__}: {exc}"

    # 退出码必须给 —— 它不在 stdout 里，是独立通道，模型判断成败唯一靠它
    parts = [f"退出码: {proc.returncode}"]
    parts.append("--- stdout ---")
    parts.append(proc.stdout or "(空)")
    parts.append("--- stderr ---")
    parts.append(proc.stderr or "(空)")
    return "\n".join(parts)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件的内容。当需要查看代码、配置、日志等文件时才用这个工具；不要凭记忆猜测文件里写了什么。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的路径，如：D:\\Dsh\\myharness\\DemoHarness.py"
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "最多读取多少个字符。这是上限不是目标，默认 8000，最大 200000。"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在命令行里执行一条命令，返回退出码和输出。当需要运行程序、跑测试、查看目录内容时才用这个工具；要看文件里写了什么请用 read_file。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令，例如：python --version",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "最多等多少秒。单位是秒，不是毫秒。默认 60。",
                    },
                },
                "required": ["command"],
            },
        },
    },
]

TOOL_IMPLS = {"read_file": read_file, "run_command": run_command}


def stream_once(messages: list[dict]) -> dict:
    stream = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        tools=TOOLS,
        temperature=0,  # agent 场景要确定性，不要创造力
        stream=True,
    )

    text_parts: list[str] = []
    calls: dict[int, dict] = {}  # index -> {id, name, arguments}
    finish_reason = None

    for chunk in stream:
        if not chunk.choices:
            continue
        if chunk.choices[0].finish_reason:  # 只有最后一个 chunk 才非空
            finish_reason = chunk.choices[0].finish_reason
        delta = chunk.choices[0].delta

        if delta.content:
            print(delta.content, end="", flush=True)
            text_parts.append(delta.content)

        for tc in delta.tool_calls or []:
            slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function and tc.function.name:
                slot["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                slot["arguments"] += tc.function.arguments  # ← 关键：累加，不覆盖

    message: dict = {"role": "assistant", "content": "".join(text_parts) or None}

    if finish_reason == "length":
        calls = {}  # 半截参数，丢掉
        text_parts.append("\n[系统注：上一次输出被长度上限截断，工具调用不完整，已丢弃]")

    if calls:
        message["tool_calls"] = [
            {
                "id": calls[i]["id"],
                "type": "function",
                "function": {"name": calls[i]["name"], "arguments": calls[i]["arguments"]},
            }
            for i in sorted(calls)
        ]
    return message


# ── ④⑤ 主循环 ────────────────────────────────────────────────
def run(task: str, max_turns: int = 6) -> None:
    messages: list[dict] = [
        {"role": "system", "content": "你是一个助手。需要信息时先调用工具，不要猜。"},
        {"role": "user", "content": task},
    ]

    for turn in range(1, max_turns + 1):
        print(f"\n─── 第 {turn} 轮 ───")
        message = stream_once(messages)
        messages.append(message)
        print(f"\n  [history {len(messages)} 条] " + " / ".join(m.get("role", "?") for m in messages))

        # ④ 模型不再请求工具 → 它认为答案已经完整了，结束
        if not message.get("tool_calls"):
            print("\n[结束] 模型给出了最终答案")
            return

        # ⑤ 执行每个工具，并把结果**回灌**给模型
        #    注意结构：每个结果是一条独立的 {"role":"tool", ...} 消息，
        #    tool_call_id 必须和上面的 id 对上，否则 API 直接报 400。
        for call in message["tool_calls"]:
            name = call["function"]["name"]
            print(f"\n  → 模型决定调用 {name}")
            # try 的范围 = 一整件事：参数解析 + 找工具 + 执行
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
                print(f"     参数: {args}")
                impl = TOOL_IMPLS.get(name)
                if impl is None:
                    result = f"错误：没有名为 {name} 的工具"
                else:
                    result = impl(**args)
            except Exception as exc:  # 参数是半截 JSON、或工具炸了，都别让程序死
                result = f"错误：{type(exc).__name__}: {exc}"
            print(f"  ← 结果: {result[:200]}（已回灌给模型）")  # [:200] 顺手治了刷屏
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result}
            )

    print("\n[停止] 达到轮数上限，可能是模型在绕圈")


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "用 python --version 告诉我这台机器的 Python 版本"
    run(task)
