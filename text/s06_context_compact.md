# 06 - s06_context_compact.py 总结

## 📌 概述

这是一个 **编码助手 Agent 的自动化循环框架**，基于 **Claude API** 实现了对话式编码代理系统。核心能力包括自动执行工具调用、管理对话上下文、压缩历史记录以突破上下文窗口限制。

---

## ⚙️ 核心功能

### 1. 工具系统（5 种工具）

| 工具名 | 功能 | 安全机制 |
|--------|------|----------|
| **bash** | 执行 shell 命令 | 黑名单保护（拦截 `rm -rf /`、`sudo`、`shutdown` 等危险命令） |
| **read_file** | 读取文件内容 | 路径逃逸安全检查，支持 limit 限制行数 |
| **write_file** | 写入文件内容 | 自动创建父目录 |
| **edit_file** | 精确替换文件文本 | 仅替换第一次出现 |
| **compact** | 手动触发对话压缩 | 可指定焦点（focus） |

### 2. 上下文管理机制

- **micro_compact** — 自动压缩早期的 `tool_result`，**只保留最近 3 个**完整结果
- **compact_history** — 调用 Claude API 对整段对话生成摘要，替换为单条压缩消息
- **CONTEXT_LIMIT = 50000** — 消息总大小超过此阈值时自动触发压缩
- **auto compact** — 每次循环前检查上下文大小，超标自动压缩

### 3. 大输出持久化

- 工具输出超过 **30,000 字符**时，自动保存到 `.task_outputs/tool-results/` 目录
- 仅保留前 **2,000 字符**的预览，完整内容通过路径引用
- 避免上下文被超大输出撑爆

### 4. 消息规范化（normalize_messages）

- 清理空消息
- 补齐缺失的 `tool_result`
- 合并连续同角色消息
- 支持 Claude block 格式与标准 dict 互转

### 5. 其他特性

| 特性 | 说明 |
|------|------|
| **write_transcript** | 每次压缩时将完整对话保存到 `.transcripts/` 目录，方便回溯 |
| **track_recent_file** | 追踪最近读取的 **5 个文件**，在压缩摘要中保留 |
| **safe_path** | 路径安全检查，防止逃逸工作目录 |
| **交互式命令行** | `s06 >>` 提示符输入查询，`q` / `exit` 退出 |

---

## 🔄 数据流

```
用户输入
    ↓
agent_loop()
    ↓
调用 Claude API（带 tools）
    ↓
解析 tool_use
    ↓
执行工具（bash / read_file / write_file / edit_file）
    ↓
返回 tool_result
    ↓
继续循环（若 stop_reason == "tool_use"）
    ↓
直到 stop_reason ≠ "tool_use"，输出最终回答
```

---

## 📦 依赖

| 依赖 | 用途 |
|------|------|
| `anthropic` | Claude API 调用 |
| `python-dotenv` | 环境变量加载 |
| `pathlib` | 路径操作 |
| `subprocess` | shell 命令执行 |
| `dataclasses` | 状态管理 |

### 环境变量

| 变量名 | 用途 |
|--------|------|
| `ANTHROPIC_BASE_URL` | Claude API 地址 |
| `MODEL_ID` | 模型标识 |

---

## 📁 关键目录结构

```
WORKDIR/
├── .task_outputs/tool-results/    # 大输出持久化目录
├── .transcripts/                  # 对话转录目录
├── s06_context_compact.py         # 主程序文件
└── ... (其他工作文件)
```

---

## 🧠 设计亮点

1. **双重压缩策略** — 微压缩（micro_compact）+ 全压缩（compact_history），兼顾效率与完整性
2. **渐进式上下文管理** — 小输出直接保留，大输出存文件 + 预览，超大输出压缩摘要
3. **安全防护** — 危险命令黑名单 + 路径逃逸检查，防止误操作
4. **可回溯性** — 每次压缩前保存完整转录，支持事后复盘
