---
name: repo-survey
description: Inspect the workspace structure, read the most relevant files, and summarize what matters before making changes.
---

# Repo Survey

Use this skill when the task starts with "look around first", "understand this repo",
or "find the right file before editing".

Workflow:
1. Use `bash` to inspect the top-level workspace and locate likely target files.
2. Use `read_file` on the smallest useful set of files instead of reading everything.
3. Identify the files that define behavior, configuration, or tests for the task.
4. Summarize the current state before proposing or making changes.

Rules:
- Prefer focused inspection over broad file dumps.
- Read code before editing code.
- Mention assumptions when the repository layout is incomplete or ambiguous.
- If you find a likely validation script, include it in your summary.

Output style:
- Keep the summary short and practical.
- Name the key files you inspected.
- Call out any missing pieces that could affect implementation or testing.
