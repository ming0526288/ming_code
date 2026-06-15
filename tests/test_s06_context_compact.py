import os
import shutil
import time
import unittest
from unittest import mock

os.environ.setdefault("MODEL_ID", "deepseek-v4-flash")

import agent.s06_context_compact as s06


class S06ContextCompactTests(unittest.TestCase):
    def setUp(self):
        self.tool_results_dir = s06.TOOL_RESULTS_DIR
        self.transcript_dir = s06.TRANSCRIPT_DIR
        self.tool_results_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.tool_results_dir.exists():
            shutil.rmtree(self.tool_results_dir)
        if self.transcript_dir.exists():
            shutil.rmtree(self.transcript_dir)

    def test_large_output(self):
        output = "A" * (s06.PERSIST_THRESHOLD + 50)

        result = s06.persist_large_output("tool-123", output)

        stored_path = self.tool_results_dir / "tool-123.txt"
        self.assertTrue(stored_path.exists())
        self.assertEqual(stored_path.read_text(encoding="utf-8"), output)
        self.assertIn("Full output saved to: .task_outputs\\tool-results\\tool-123.txt", result)
        self.assertIn("Preview:", result)
        self.assertIn("A" * s06.PREVIEW_CHARS, result)
        self.assertNotEqual(result, output)

    def test_tool(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"id-{index}",
                        "content": f"result-{index}-" + ("x" * 150),
                    }
                ],
            }
            for index in range(s06.KEEP_RECENT_TOOL_RESULTS + 1)
        ]

        compacted = s06.micro_compact(messages)

        self.assertEqual(
            compacted[0]["content"][0]["content"],
            "[Earlier tool result compacted. Re-run the tool if you need full detail.]",
        )
        for index in range(1, s06.KEEP_RECENT_TOOL_RESULTS + 1):
            self.assertIn("result-", compacted[index]["content"][0]["content"])

    def test_long_history(self):
        messages = [
            {"role": "user", "content": "please continue"},
            {"role": "assistant", "content": "working on it"},
        ]
        state = s06.CompactState(recent_files=["agent/s06_context_compact.py", "text/s06_context_compact.md"])

        before_files = set(self.transcript_dir.glob("transcript_*.jsonl"))
        with mock.patch.object(s06, "summarize_history", return_value="summary text"):
            compacted = s06.compact_history(messages, state, focus="preserve tests")
        time.sleep(0.01)
        after_files = set(self.transcript_dir.glob("transcript_*.jsonl"))

        self.assertEqual(len(compacted), 1)
        self.assertEqual(compacted[0]["role"], "user")
        compacted_text = compacted[0]["content"]
        self.assertIn("This conversation was compacted", compacted_text)
        self.assertIn("summary text", compacted_text)
        self.assertIn("Focus to preserve next: preserve tests", compacted_text)
        self.assertIn("- agent/s06_context_compact.py", compacted_text)
        self.assertIn("- text/s06_context_compact.md", compacted_text)
        self.assertTrue(state.has_compacted)
        self.assertIn("summary text", state.last_summary)
        self.assertGreater(len(after_files - before_files), 0)


if __name__ == "__main__":
    unittest.main()
