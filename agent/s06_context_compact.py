from dotenv import load_dotenv
import os
from anthropic import Anthropic
from pathlib import Path
import subprocess
from dataclasses import dataclass, field
import time
import json

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd().resolve() # current work directory当前文件目录
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]


SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "Keep working step by step, and use compact if the conversation gets too long."
)

PERSIST_THRESHOLD = 30000 # 持久保存 阈值
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
PREVIEW_CHARS = 2000
KEEP_RECENT_TOOL_RESULTS = 3 # 只保留最近3个工具结果的完整内容
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
CONTEXT_LIMIT = 50000

def persist_large_output(tool_use_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stored_path = TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
    if output and (not stored_path.exists() or stored_path.stat().st_size == 0):
        stored_path.write_text(output, encoding="utf-8")
    preview = output[:PREVIEW_CHARS]
    rel_path = stored_path.relative_to(WORKDIR)
    return (
        "<persisted-output>\n"
        f"Full output saved to: {rel_path}\n"
        "Preview:\n"
        f"{preview}\n"
        "</persisted-output>"
    )

def collect_tool_result_blocks(messages: list) -> list[tuple[int, int, dict]]:
    blocks = []
    for message_index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                blocks.append((message_index, block_index, block))
    return blocks

def micro_compact(messages: list) -> list:
    tool_results = collect_tool_result_blocks(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    for _, _, block in tool_results[: -KEEP_RECENT_TOOL_RESULTS]:
        content = block.get("content", "")
        if not isinstance(content, str) or len(content) <= 120:
            continue
        block["content"] = "[Earlier tool result compacted. Re-run the tool if you need full detail.]"
    return messages
    
@dataclass
class CompactState:
    has_compacted: bool = False
    last_summary: str = ""
    recent_files: list[str] = field(default_factory=list)

def write_transcript(messages: list) -> Path:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(json.dumps(message, default=str) + "\n")
    return path

def extract_text(content):
    texts = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts).strip()

def summarize_history(messages: list) -> str:
    conversation = json.dumps(messages, default=str)[: 80000]
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve:\n"
        "1. The current goal\n"
        "2. Important findings and decisions\n"
        "3. Files read or changed\n"
        "4. Remaining work\n"
        "5. User constraints and preferences\n"
        "Be compact but concrete.\n\n"
        f"{conversation}"
    )
    response = client.messages.create(
        model = MODEL,
        messages = [{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    return extract_text(response.content).strip()

def compact_history(messages: list, state: CompactState, focus: str | None = None) -> list:
    transcript_path = write_transcript(messages)
    print(f"[transcript saved: {transcript_path}]")
    summary = summarize_history(messages)
    if focus:
        summary += f"\n\nFocus to preserve next: {focus}"
    if state.recent_files:
        recent_lines = "\n".join(f"- {path}" for path in state.recent_files)
        summary += f"\n\nRecent files to reopen if needed:\n{recent_lines}"
    state.has_compacted = True
    state.last_summary = summary
    return [{
        "role": "user",
        "content": (
            "This conversation was compacted so the agent can continue working.\n\n"
            f"{summary}"
        )
    }]

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str, tool_use_id: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    output = (stdout + stderr).strip()
    return persist_large_output(tool_use_id, output) if output else "no output"

def track_recent_file(state: CompactState, path: str) -> None:
    if path in state.recent_files:
        state.recent_files.remove(path)
    state.recent_files.append(path)
    if len(state.recent_files) > 5:
        state.recent_files[:] = state.recent_files[-5:] # 最后五个

def run_read(path: str, tool_use_id: str, state: CompactState,limit: int | None = None) -> str:
    try:
        track_recent_file(state, path)
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"...({len(lines) - limit} more lines)"]
        output = "\n".join(lines)
        return persist_large_output(tool_use_id, output)
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
        content = file_path.read_text(encoding="utf-8")
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"
    
def execute_tool(block, state: CompactState) -> str:
    if block.name == "bash":
        return run_bash(block.input["command"], block.id)
    if block.name == "read_file":
        return run_read(block.input["path"], block.id, state, block.input.get("limit"))
    if block.name == "write_file":
        return run_write(block.input["path"], block.input["content"])
    if block.name == "edit_file":
        return run_edit(block.input["path"], block.input["old_text"], block.input["new_text"])
    if block.name == "compact":
        return "Compacting conversation..."
    return f"Unknown tool: {block.name}"

TOOLS = [
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
    {
        "name": "compact",
        "description": "Summarize earlier conversation so work can continue in a smaller context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {"type": "string"},
            },
        },
    },
]

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

def estimate_content_size(messages: list) -> int:
    return len(str(messages))

def agent_loop(messages: list, state: CompactState):
    while True:
        messages[:] = micro_compact(messages)
        if estimate_content_size(messages) > CONTEXT_LIMIT:
            print("auto compact")
            messages[:] = compact_history(messages, state)
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=normalize_messages(messages),
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content" : response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        manual_compact = False
        compact_focus = None
        for block in response.content:
            if block.type != "tool_use":
                continue
            output = execute_tool(block, state)
            if block.name == "compact":
                manual_compact = True
                compact_focus = (block.input or {}).get("focus")
            print(f"\033[33m> {block.name}: {block.input}\033[0m")
            print(output[:200])
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
        if manual_compact:
            print("[manual compact]")
            messages[:] = compact_history(messages, state, focus=compact_focus)

if __name__ == "__main__":
    history = []
    compact_state = CompactState()
    while True:
        try:
            query = input("\033[36ms06 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history, compact_state)
        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()