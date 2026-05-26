import os
from anthropic import Anthropic
from dotenv import load_dotenv
from dataclasses import dataclass
import subprocess

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

# os.getcwd()是获取当前工作目录
SYSTEM = (
    f"You are a coding agent at {os.getcwd()}."
    "Use bash to inspect and change the workspace. Act first, then report clearly."
)

TOOLS = [{
    "name" :"bash",
    "description": "Run a shell command in the current workspace.",
    "input_schema":{
        "type" : "object",
        "properties": {"command":{"type": "string"}},
        "required": ["command"],
    },
}]

@dataclass
class LoopState:
    messages: list                           # 聊天记录列表
    transition_reason : str | None = None    # 状态转换原因， 默认为None
    turn_count : int = 1                     # 当前对话轮数，默认为1

def run_bash(command: str) -> str:
    result = subprocess.run(
        command, 
        shell=True, 
        cwd=os.getcwd(),
        capture_output=True, 
        text=True,
        timeout=120,
    )
    return result.stdout + result.stderr # 正常输出 + 错误/警告信息

def agent_loop(state: LoopState):
    while True:
        response = client.messages.create(
            model = MODEL,
            system = SYSTEM,
            messages = state.messages,
            tools = TOOLS,
            max_tokens = 8000
        )
        state.messages.append({
            "role" : "assistant", 
            "content": response.content
        })

        if response.stop_reason != "tool_use":
            state.transition_reason = None
            return
        
        results= []
        for block in response.content:
            if block.type == "tool_use":
                command = block.input["command"]
                out_put = run_bash(command)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": out_put,
                })
        state.messages.append({"role": "user", "content": results})
        state.turn_count += 1
        state.transition_reason = "tool_result"

def extract_text(content) -> list[dict]:
    texts = []
    for block in content:
        if getattr(block, "type", None) == "text":
            texts.append(block.text)

    return "\n".join(texts)


if __name__ == "__main__":
    history = []
    query = input()
    history.append({"role": "user", "content": query})
    state = LoopState(messages=history)
    agent_loop(state)
    response_content = history[-1]["content"]
    final_text = extract_text(response_content)
    print(final_text)