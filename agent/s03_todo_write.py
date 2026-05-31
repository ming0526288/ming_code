from dotenv import load_dotenv
import os
from pathlib import Path
from anthropic import Anthropic
import subprocess
from dataclasses import dataclass, field

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd().resolve()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
PLAN_REMINDER_INTERVAL = 3

SYSTEM = f"""You are a coding agent at {WORKDIR}.
Use the todo tool for multi-step work.
Keep exactly one step in_progress when a task has multiple steps.
Refresh the plan as work advances. Prefer tools over prose."""

def run_bash(command: str) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    output = (stdout + stderr).strip()
    return output[:50000] if output else "no output"

def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve() # .resolve()就是把当前工作目录转成规范的绝对路径
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_str}")
    return path

def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"...({len(lines) - limit})"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error:{e}"
    
def run_write(path:str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        return f"wrote {byte_count} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"
    
def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

TOOL_HANDLERS = {
    "bash" : lambda **kw: run_bash(kw["command"]),
    "read_file" : lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file" : lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file" : lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo": lambda **kw: TODO.update(kw["items"]),
}

TOOLS = [
    {
        "name": "bash",
        "description": " Run a shell command.",
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
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {
                                "type": "string",
                                "description": "Optional present-continuous label.",
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["items"],
        },
    },
]

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

            normalized.append(PlanItem(
                content=content,
                status=status,
                active_form=active_form,
            ))

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
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[item.status]
            line = f"{marker} {item.content}"
            if item.status == "in_progress" and item.active_form:
                line += f" ({item.active_form})"
            lines.append(line)

        completed = sum(1 for item in self.state.items if item.status == "completed")
        lines.append(f"\n({completed}/{len(self.state.items)} completed)")
        return "\n".join(lines)


TODO = TodoManager()

def block_to_dict(block):
    if isinstance(block, dict):
        raw = block
    elif hasattr(block, "model_dump"):
        raw = block.model_dump()
    else:
        return {
            "type": "text",
            "text": str(block)
        }
    clean = {
        k: v for k, v in raw.items()
        if k not in ("_internal", "_source", "_timestamp")
        and v is not None
    }
    return clean


def normalize_messages(messages: list) -> list:
    normalized = []

    # Step 1: 清理消息
    for message in messages:
        clean = {"role": message["role"]}
        content = message.get("content")
        if isinstance(content, str):
            if not content.strip():
                continue
            clean["content"] = content
        elif isinstance(content, list):
            blocks = []
            for block in content:
                clean_block = block_to_dict(block)
                if clean_block is not None:
                    blocks.append(clean_block)
            if not blocks:
                continue
            clean["content"] = blocks
        else:
            continue
        normalized.append(clean)

    # Step 2: 收集已有 tool_result
    existing_results = set()
    for message in normalized:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    existing_results.add(block.get("tool_use_id"))

    # Step 3: 补齐缺失 tool_result
    fixed = []
    for message in normalized:
        fixed.append(message)
        content = message.get("content")
        if message["role"] == "assistant" and isinstance(content, list):
            missing_results = []
            for block in content:
                if block.get("type") == "tool_use" and block.get("id") not in existing_results:
                    missing_results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": "(cancelled)"
                    })
            if missing_results:
                fixed.append({"role": "user","content": missing_results})
    if not fixed:
        return []

    # Step 4: 合并连续同角色消息
    merged = [fixed[0]]
    for message in fixed[1:]:
        if message["role"] == merged[-1]["role"]:
            prev = merged[-1] # prev 是 previous message
            prev_content = (
                prev["content"]
                if isinstance(prev["content"], list)
                else [{"type": "text", "text": prev["content"]}]
            )
            curr_content = (
                message["content"]
                if isinstance(message["content"], list)
                else [{"type": "text", "text": message["content"]}]
            )
            prev["content"] = prev_content + curr_content
        else:
            merged.append(message)

    # Step 5: 最后再过滤一次空消息
    merged = [
        m for m in merged
        if m.get("content")
    ]

    return merged

def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=normalize_messages(messages),
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        used_todo = False
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
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

def extract_text(content) -> str:
    if not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        text = getattr(block, "text", None) # get attribute 获取某个属性
        if text:
            texts.append(text)
    return "\n".join(texts).strip()
    
if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
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
