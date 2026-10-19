# myharness

> 我想搞明白 agent 到底是怎么运转的，所以从零写了一个，边写边记。
>
> 这不是给生产环境用的框架，是一份**学习记录**。所以它刻意保持小、保持蠢、保持每一行都能读懂。

**236 行，零框架。** 没有 LangChain，没有 LangGraph，核心只有一个 `while` 循环。

---

## 先看它跑起来

```
$ python DemoHarness.py "用 python --version 告诉我这台机器的 Python 版本"

─── 第 1 轮 ───

  [history 3 条] system / user / assistant

  → 模型决定调用 run_command
     参数: {'command': 'python --version'}
  ← 结果: 退出码: 0
--- stdout ---
Python 3.10.11

--- stderr ---
(空)

─── 第 2 轮 ───
这台机器的 Python 版本是 **Python 3.10.11**。
  [history 5 条] system / user / assistant / tool / assistant

[结束] 模型给出了最终答案
```

第 1 轮的 history 有 3 条，第 2 轮有 5 条。多出来的那两条——一条 `assistant`（模型说要调工具）、一条 `tool`（装着命令的真实输出）——**就是整个 harness 在干的事**。

---

## 这是什么

一个 **agent harness**：不是模型，也不是框架，而是「模型 + 循环 + 记忆」跑起来所需要的那套身体和规矩。

- **模型** 是智力。一个函数：文本进，文本出，每次调用都失忆。
- **agent** 是行为。是 `run()` 转起来之后出现的那个东西，跑完就没了。
- **harness** 是这 236 行。让上面那个过程能存在、能动手、不出事。

同一个模型放进不同的 harness，就是完全不同的 agent。这个项目做的就是 harness 那一层。

---

## 环境要求

- Python **3.9+**（我在 3.10.11 上测的）
- 一个依赖：`openai`
- 一个 API Key（默认用 DeepSeek，[申请地址](https://platform.deepseek.com/api_keys)）

## 快速开始

```powershell
# 1. 装唯一的依赖
pip install openai

# 2. 配 key（.env 不会被提交，见 .gitignore）
copy .env.example .env
#    然后打开 .env，把 DEEPSEEK_API_KEY 换成你自己的

# 3. 跑
python DemoHarness.py "用 python --version 告诉我这台机器的 Python 版本"
```

不传参数就会跑默认任务。

⚠️ Windows 上别忘了用**完整路径**调 python，或者先确认 `python` 指向你想要的那个解释器。

---

## 它是怎么工作的

整个 harness 就是这几行：

```
messages = [system 说规矩, user 说任务]

最多重复 max_turns 次：
    ① 带上 TOOLS 说明书问模型，把流式碎片拼成一条完整消息
    ② 把这条消息记进 history
    ③ 模型没要求工具 → 它说完了，结束
    ④ 模型要求了 → 查表执行，每个结果作为一条 role:"tool" 消息记进 history
    ⑤ 回到 ①

达到上限 → 提示「可能在绕圈」
```

### 四个部件

| 部件 | 位置 | 作用 |
|---|---|---|
| `TOOLS` | 89-132 行 | 给**模型**的说明书（它读这个决定调谁） |
| `TOOL_IMPLS` | 134 行 | 给**代码**的电话表（模型只说名字，你查表真干） |
| `stream_once()` | 137-188 行 | 把流式碎片拼成一条完整消息 |
| `run()` | 192-231 行 | 问 → 记 → 执行 → 回灌 → 再问 |

### 一个关键细节：谁决定什么时候停？

```python
if not message.get("tool_calls"):   # ← 退出条件是模型给的，不是代码写死的
    return
```

`for turn in range(...)` 长得像固定流程，但它只是**安全阀**。真正的方向盘在模型手里——这就是「agent」和「自动化脚本」的分界线。

---

## 建议的阅读顺序

别从头往下读，会晕。按这个顺序：

1. **`run()`（39 行）** —— 整个 harness 就这么多，先看骨架
2. **`stream_once()`（41 行）** —— 搞清「流式碎片」怎么变成「一条完整消息」
3. **`TOOLS` / `TOOL_IMPLS`** —— 说明书和电话表，理解模型是怎么"伸出手"的
4. **`read_file()`** —— 最后看。它 34 行里只有 3 个想法是真设计（`max_chars + 1` 当探针、先截断再判断、`notes` 列表因为多种异常可同时发生），其余全是在跟 Windows 的编码和文件系统打架

---

## 它能做什么 / 不能做什么

能：

- ✅ 调用模型、执行工具、把结果回灌、循环到结束
- ✅ `read_file` —— 带截断、二进制检测、非 UTF-8 警告、权限处理
- ✅ `run_command` —— 带退出码、stderr 分离、超时保护

不能（这是诚实的边界）：

- ❌ 还不能写文件（下一步）
- ❌ 没有权限系统（模型可以执行任意命令）
- ❌ 没有会话持久化（关掉就失忆）
- ❌ 没有上下文压缩（history 会一直长到撑爆）
- ❌ 没有子 agent、没有 hooks、没有 hooks 之外的任何"生产级"特性

### 已知问题

- `run_command` 的 `timeout` 只杀直接子进程，不杀它的子孙进程
- `run_command` 的输出没有长度上限（`dir /s` 一条就 2.5 MB，够把上下文一拳打死）
- 没有接 `finish_reason`：如果输出被 `max_tokens` 掐断，工具参数会是半截 JSON。现在靠 `try` 兜住（程序不会死），但模型会以为是自己的错并重试

---

## 换模型厂商

`DemoHarness.py` 里改两处就行：

```python
# 第 19-22 行
client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",   # ← 改这里
)

# 第 139 行
model="deepseek-chat",                      # ← 改这里
```

Kimi / 智谱 / 通义 / 本地 Ollama 只要兼容 OpenAI 协议都能换。

---

## 我踩过的坑

写这份代码的过程中踩到的，每一个都比代码本身值得记：

- **`content = ...` 不报错** —— 赋值语句永远不会失败，所以 `try/except` 根本没接住它。程序一路跑到下次 API 调用，才在完全无关的地方炸，报错位置全是误导。
- **`"type": "long"` 让 API 直接 400** —— JSON Schema 里没有 `long`，只有 `string` / `number` / `integer` / `boolean` / `object` / `array` / `null`。而这个错误只有真的发一次请求才会出现。
- **`errors="replace"` 不能省** —— 省了它，解码线程会死，`p.stdout` 变成 `None`，报错还指向别的地方。Windows 上默认用 GBK 解码，撞上一个不是 UTF-8 的字节就整条命令结果全丢。
- **`json.loads` 成功 ≠ 模型说完了** —— 输出被 `max_tokens` 掐断时，`finish_reason` 会是 `length`，但参数串碰巧可能仍然是合法 JSON。只看 `json.loads` 是否抛异常，会漏掉这一类。
- **`try` 该圈「一整件事」** —— 我一开始只把 `impl(**args)` 放进 `try`，结果参数解析本身就能炸。`try` 的范围应该是「从模型给的参数，到工具跑完」。

详细过程会陆续整理进 `journal/`。

---

## License

MIT，详见 [LICENSE](LICENSE)。
