"""
s04_subagent.py — 工具使用代理引擎（含子代理委派）
基于 s03_todo_write.py，新增：
  - SubAgent 类：独立运行的自包含子代理
  - delegate 工具：主代理委派任务给子代理
  - 子代理独立消息历史 / Todo 计划 / 工具环境
  - 结果收集与返回
"""

from dotenv import load_dotenv
import os
import json
import subprocess
import uuid
import time
from pathlib import Path
from dataclasses import dataclass, field
from anthropic import Anthropic

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd().resolve()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
PLAN_REMINDER_INTERVAL = 3

# ============================================================
# 系统提示
# ============================================================
MAIN_SYSTEM = f"""You are a coding agent at {WORKDIR}.
You have a **delegate** tool that lets you spawn sub-agents for subtasks.

Use sub-agents when:
- A task can be done in parallel with your current work
- A subtask is self-contained and independent
- You want to keep your main plan focused

After delegating, use the todo tool to track the sub-task.
Prefer tools over prose."""

SUB_SYSTEM = f"""You are a sub-agent working at {WORKDIR}.
Your task has been delegated by a parent agent.
Complete your assigned task using the available tools.
When done, use the finish tool to return your result.
Stay focused on your assigned task only."""

# ============================================================
# 路径安全
# ============================================================
def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path

# ============================================================
# 基础工具函数
# ============================================================
def run_bash(command: str) -> str:
    try:
        result = subprocess.run(
            command, shell=True, cwd=WORKDIR,
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    output = (stdout + stderr).strip()
    return output[:50000] if output else "no output"

def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"...({len(lines) - limit})"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error:{e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        return f"wrote {byte_count} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

# ============================================================
# 基础工具注册表（主代理与子代理共享）
# ============================================================
BASE_TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

BASE_TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exact text in a file once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
]

# ============================================================
# Todo 计划管理（复用 s03）
# ============================================================
@dataclass
class PlanItem:
    content: str
    status: str = "pending"
    active_form: str = ""

@dataclass
class PlanningState:
    items: list[PlanItem] = field(default_factory=list)
    rounds_since_update: int = 0

class TodoManager:
    def __init__(self):
        self.state = PlanningState()

    def update(self, items: list) -> str:
        if len(items) > 12:
            raise ValueError("Keep the session plan short (max 12 items)")
        normalized = []
        in_progress_count = 0
        for index, raw_item in enumerate(items):
            content = str(raw_item.get("content", "")).strip()
            status = str(raw_item.get("status", "pending")).lower()
            active_form = str(raw_item.get("activeForm", "")).strip()
            if not content:
                raise ValueError(f"Item {index}: content required")
            if status not in {"pending", "in_progress", "completed"}:
                raise ValueError(f"Item {index}: invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
            normalized.append(PlanItem(content=content, status=status, active_form=active_form))
        if in_progress_count > 1:
            raise ValueError("Only one plan item can be in_progress")
        self.state.items = normalized
        self.state.rounds_since_update = 0
        return self.render()

    def note_round_without_update(self) -> None:
        self.state.rounds_since_update += 1

    def reminder(self) -> str | None:
        if not self.state.items:
            return None
        if self.state.rounds_since_update < PLAN_REMINDER_INTERVAL:
            return None
        return "<reminder>Refresh your current plan before continuing.</reminder>"

    def render(self) -> str:
        if not self.state.items:
            return "No session plan yet."
        lines = []
        for item in self.state.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item.status]
            line = f"{marker} {item.content}"
            if item.status == "in_progress" and item.active_form:
                line += f" ({item.active_form})"
            lines.append(line)
        completed = sum(1 for item in self.state.items if item.status == "completed")
        lines.append(f"\n({completed}/{len(self.state.items)} completed)")
        return "\n".join(lines)

# ============================================================
# 消息规范化（复用 s03）
# ============================================================
def block_to_dict(block):
    if isinstance(block, dict):
        raw = block
    elif hasattr(block, "model_dump"):
        raw = block.model_dump()
    else:
        return {"type": "text", "text": str(block)}
    return {k: v for k, v in raw.items()
            if k not in ("_internal", "_source", "_timestamp") and v is not None}

def normalize_messages(messages: list) -> list:
    normalized = []
    for message in messages:
        clean = {"role": message["role"]}
        content = message.get("content")
        if isinstance(content, str):
            if not content.strip():
                continue
            clean["content"] = content
        elif isinstance(content, list):
            blocks = [block_to_dict(b) for b in content if block_to_dict(b)]
            if not blocks:
                continue
            clean["content"] = blocks
        else:
            continue
        normalized.append(clean)
    existing_results = set()
    for message in normalized:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    existing_results.add(block.get("tool_use_id"))
    fixed = []
    for message in normalized:
        fixed.append(message)
        content = message.get("content")
        if message["role"] == "assistant" and isinstance(content, list):
            missing_results = []
            for block in content:
                if block.get("type") == "tool_use" and block.get("id") not in existing_results:
                    missing_results.append({
                        "type": "tool_result", "tool_use_id": block["id"], "content": "(cancelled)"
                    })
            if missing_results:
                fixed.append({"role": "user", "content": missing_results})
    if not fixed:
        return []
    merged = [fixed[0]]
    for message in fixed[1:]:
        if message["role"] == merged[-1]["role"]:
            prev = merged[-1]
            prev_content = prev["content"] if isinstance(prev["content"], list) else [{"type": "text", "text": prev["content"]}]
            curr_content = message["content"] if isinstance(message["content"], list) else [{"type": "text", "text": message["content"]}]
            prev["content"] = prev_content + curr_content
        else:
            merged.append(message)
    merged = [m for m in merged if m.get("content")]
    return merged

def extract_text(content) -> str:
    if not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts).strip()

# ============================================================
# 子代理系统核心
# ============================================================

# 全局子代理注册表
_sub_agents: dict[str, "SubAgent"] = {}

class SubAgent:
    """自包含的子代理，独立运行自己的 agent_loop"""

    def __init__(self, agent_id: str, task: str, max_turns: int = 15):
        self.agent_id = agent_id
        self.task = task
        self.max_turns = max_turns
        self.turns = 0
        self.messages: list = []
        self.todo = TodoManager()
        self.status = "running"  # running | finished | error
        self.result = ""
        self.error = ""
        self.log: list[str] = []

    def log_msg(self, msg: str):
        self.log.append(f"[{self.agent_id}] {msg}")

    def agent_loop(self):
        """子代理独立循环"""
        self.messages.append({"role": "user", "content": self.task})

        while self.turns < self.max_turns:
            try:
                response = client.messages.create(
                    model=MODEL,
                    system=SUB_SYSTEM,
                    messages=normalize_messages(self.messages),
                    tools=SUB_AGENT_TOOLS,
                    max_tokens=8000,
                )
            except Exception as e:
                self.status = "error"
                self.error = f"API error: {e}"
                self.log_msg(f"API error: {e}")
                return

            self.messages.append({"role": "assistant", "content": response.content})

            # 如果模型结束对话（end_turn 或 finish 工具调用）
            if response.stop_reason != "tool_use":
                # 收集最终回复文本作为结果
                final = extract_text(response.content)
                if final:
                    self.result = final
                self.status = "finished"
                self.log_msg(f"Finished after {self.turns + 1} turns")
                return

            # 处理工具调用
            results = []
            used_todo = False
            used_finish = False
            for block in response.content:
                if block.type == "tool_use":
                    handler = SUB_AGENT_HANDLERS.get(block.name)
                    if handler:
                        try:
                            output = handler(**block.input)
                        except Exception as e:
                            output = f"Error: {e}"
                    else:
                        output = f"Unknown tool: {block.name}"

                    # 子代理日志打印（缩进以示区别）
                    print(f"  \033[35m[s04:{self.agent_id[:6]}] {block.name}: {str(output)[:150]}\033[0m")

                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

                    if block.name == "todo":
                        used_todo = True
                    if block.name == "finish":
                        used_finish = True

            # 计划提醒
            if used_todo:
                self.todo.state.rounds_since_update = 0
            else:
                self.todo.note_round_without_update()

            self.messages.append({"role": "user", "content": results})

            # 如果调用了 finish，收集结果后退出
            if used_finish:
                self.status = "finished"
                self.log_msg(f"Finished via finish tool after {self.turns + 1} turns")
                return

            # 提醒
            reminder = self.todo.reminder()
            if reminder:
                self.messages.append({"role": "user", "text": reminder})

            self.turns += 1

        # 超出最大轮数
        self.status = "finished"
        self.result = self.result or "(reached max turns without finishing)"
        self.log_msg(f"Reached max turns ({self.max_turns})")

    def get_summary(self) -> str:
        """返回子代理执行摘要"""
        status_icon = {"running": "[>]", "finished": "[x]", "error": "[!]"}.get(self.status, "[?]")
        lines = [
            f"Sub-agent: {self.agent_id}",
            f"Status: {self.status}",
            f"Task: {self.task[:200]}",
            f"Turns used: {self.turns}/{self.max_turns}",
        ]
        if self.result:
            lines.append(f"Result: {self.result[:500]}")
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)


# ============================================================
# 子代理工具定义
# ============================================================

# 子代理的 finish 工具
def run_finish(result: str = ""):
    """子代理完成任务后调用，返回结果"""
    return f"[FINISH] {result}" if result else "[FINISH] Task completed."

SUB_AGENT_HANDLERS = {
    **BASE_TOOL_HANDLERS,
    "todo":   lambda **kw: _current_sub_agent.todo.update(kw["items"]) if _current_sub_agent else "Error: no sub-agent context",
    "finish": lambda **kw: run_finish(kw.get("result", "")),
}

SUB_AGENT_TOOLS = [
    *BASE_TOOLS,
    {
        "name": "todo",
        "description": "Rewrite the current session plan for multi-step work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            "activeForm": {"type": "string", "description": "Optional present-continuous label."},
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "finish",
        "description": "Call this when the delegated task is complete. Provide a result summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "result": {"type": "string", "description": "Summary of what was accomplished"},
            },
            "required": [],
        },
    },
]

# ============================================================
# 主代理的 delegate 工具
# ============================================================
_current_sub_agent: SubAgent | None = None

def run_delegate(task: str, max_turns: int = 15) -> str:
    """主代理委派任务给子代理"""
    global _current_sub_agent
    agent_id = f"sub-{uuid.uuid4().hex[:8]}"

    sub = SubAgent(agent_id=agent_id, task=task, max_turns=max_turns)
    _sub_agents[agent_id] = sub

    print(f"\033[35m[s04] Spawning sub-agent {agent_id} for task: {task[:100]}...\033[0m")

    # 记录当前子代理上下文（供 finish 工具使用）
    _current_sub_agent = sub

    # 同步运行子代理（阻塞）
    sub.agent_loop()

    # 清理上下文
    _current_sub_agent = None

    summary = sub.get_summary()
    print(f"\033[35m[s04] Sub-agent {agent_id} finished.\033[0m")
    return summary


def run_get_result(agent_id: str) -> str:
    """查询已完成的子代理结果"""
    sub = _sub_agents.get(agent_id)
    if not sub:
        return f"Error: No sub-agent found with id '{agent_id}'"
    return sub.get_summary()


# ============================================================
# 主代理工具注册
# ============================================================
TODO = TodoManager()

MAIN_HANDLERS = {
    **BASE_TOOL_HANDLERS,
    "todo":       lambda **kw: TODO.update(kw["items"]),
    "delegate":   lambda **kw: run_delegate(kw["task"], kw.get("max_turns", 15)),
    "get_result": lambda **kw: run_get_result(kw["agent_id"]),
}

MAIN_TOOLS = [
    *BASE_TOOLS,
    {
        "name": "todo",
        "description": "Rewrite the current session plan for multi-step work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            "activeForm": {"type": "string", "description": "Optional present-continuous label."},
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "delegate",
        "description": "Delegate a self-contained subtask to a sub-agent. The sub-agent has its own tools and todo plan. Use this for tasks that can be done independently or in parallel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear description of the task for the sub-agent",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Maximum turns before forced return (default 15)",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "get_result",
        "description": "Get the result of a previously delegated sub-agent by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The ID of the sub-agent (returned from delegate)",
                },
            },
            "required": ["agent_id"],
        },
    },
]

# ============================================================
# 主代理循环
# ============================================================
def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL,
            system=MAIN_SYSTEM,
            messages=normalize_messages(messages),
            tools=MAIN_TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return

        results = []
        used_todo = False
        for block in response.content:
            if block.type == "tool_use":
                handler = MAIN_HANDLERS.get(block.name)
                if handler:
                    try:
                        output = handler(**block.input)
                    except Exception as e:
                        output = f"Error: {e}"
                else:
                    output = f"Unknown tool: {block.name}"
                print(f"\033[33m> {block.name}: {str(output)[:200]}\033[0m")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                if block.name == "todo":
                    used_todo = True

        if used_todo:
            TODO.state.rounds_since_update = 0
        else:
            TODO.note_round_without_update()

        messages.append({"role": "user", "content": results})

        reminder = TODO.reminder()
        if reminder:
            messages.append({"role": "user", "text": reminder})


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    print(f"\033[35m[s04] Sub-agent engine started. WORKDIR={WORKDIR}\033[0m")
    print(f"\033[35m[s04] Use 'delegate' tool to spawn sub-agents for subtasks.\033[0m")

    history = []
    while True:
        try:
            query = input("\033[36ms04 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        history.append({"role": "user", "content": query})
        agent_loop(history)

        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()
