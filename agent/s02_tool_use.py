from dotenv import load_dotenv
import os
from anthropic import Anthropic
from pathlib import Path
import subprocess

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd().resolve() # current work directory当前文件目录
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

# print(Path.cwd())
# print(type(Path.cwd()))

# print(os.getcwd())
# print(type(os.getcwd()))

SYSTEM = (
    f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. Act, don't explain."
)

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}") # 路径越狱跳出工作区
    return path

def run_read(path: str, limit:int = None) ->str:
    try:
        file_path = safe_path(path) # 文件路径
        text = file_path.read_text(encoding="utf-8", errors="replace") # 读取文本内容
        lines = text.splitlines() # 按行拆开
        if limit and limit < len(lines): # 如果传入limit，文件行数超过limit，只保留limit行
            lines = lines[:limit] 
        return "\n".join(lines)[:50000] # 最多返回前50000字符
    except Exception as e:
        return f"Error:{e}"

def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True) # parent代表父目录
        file_path.write_text(content, encoding="utf-8") # 字符串写入文件
        byte_count = len(content.encode("utf-8"))
        return f"Wrote {byte_count} bytes to {path}"
        # return f"Wrote {len(content)} characters to {path}" 这个返回的是字符数
    except Exception as e:
        return f"Error:{e}"

def run_edit(path:str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text(encoding="utf-8", errors="replace")
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8") # 1代表替换一次
        return f"Edited {path}"
    except Exception as e:
        return f"Error:{e}"
    
import locale

def decode_output(data: bytes) -> str:
    if not data:
        return ""
    encodings = [
        "utf-8",
        "utf-8-sig",
        "gbk",
        "cp936",
        locale.getpreferredencoding(False),
    ]
    for enc in encodings:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def run_bash(command: str) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=False,
            timeout=120
        )
        stdout = decode_output(result.stdout)
        stderr = decode_output(result.stderr)
        out = (stdout + stderr).strip()
        if not out:
            out = "(no output)"
        return out[:50000]
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except Exception as e:
        return f"Error: {e}"
    
# -- The dispatch map: {tool_name: handler} --
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),    # kw : keyword
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
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

def agent_loop(messages: list):
    while True:
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
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                print(f"\033[33m> {block.name}: {block.input}\033[0m")
                print(output[:200])
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})

if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"): # has attribute 是否有某个属性
                    print(block.text)
        print()