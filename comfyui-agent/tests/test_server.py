from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server" / "src"))

from comfyui_agent_bridge_mcp import server  # noqa: E402
from comfyui_agent_bridge_mcp.client import BridgeHttpError  # noqa: E402


class FakeClient:
    def __init__(self, response=None):
        self.response = response or {}
        self.calls = []

    async def object_info(self, class_type=None):
        self.calls.append(("object_info", class_type))
        if class_type:
            return {class_type: self.response}
        return self.response

    async def command(self, action, payload, **kwargs):
        self.calls.append(("command", action, payload, kwargs))
        return {"command_id": "history-123", "result": {"ok": True}}


class ServerToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_status_preserves_partial_reads_and_source_errors(self):
        class PartialClient:
            base_url = "http://comfy.invalid"

            async def bridge_status(self):
                raise BridgeHttpError(503, "bridge_down", "status endpoint failed")

            async def system_stats(self):
                return {"devices": ["gpu0"]}

            async def queue(self):
                raise BridgeHttpError(0, "comfyui_unreachable", "queue unavailable")

            async def recent_errors(self):
                return {"ok": True, "count": 0, "errors": []}

        with patch.object(server, "_client", return_value=PartialClient()):
            result = await server.bridge_status()

        self.assertIsNone(result["bridge"])
        self.assertEqual(result["system"], {"devices": ["gpu0"]})
        self.assertIsNone(result["queue"])
        self.assertEqual(result["recent_errors"]["count"], 0)
        self.assertEqual(result["diagnostics"]["bridge"]["error"]["source"], "comfyui.bridge_status")
        self.assertEqual(result["diagnostics"]["bridge"]["error"]["code"], "bridge_down")
        self.assertEqual(result["diagnostics"]["queue"]["error"]["source"], "comfyui.queue")
        self.assertTrue(result["diagnostics"]["recent_errors"]["ok"])

    async def test_raw_session_and_recent_history_preserve_full_state_and_errors(self):
        history = {
            "prompt-7": {
                "status": {
                    "status_str": "error",
                    "completed": False,
                    "messages": [["execution_error", {"exception_message": "raw traceback"}]],
                },
                "outputs": {},
            }
        }

        class ReadClient:
            def __init__(self):
                self.calls = []

            async def session(self, session_id=None, raw=False):
                self.calls.append(("session", session_id, raw))
                return {"session_id": session_id, "workflow": {"nodes": [{"id": 1}]}, "view": {"nodes": []}}

            async def recent_history(self, max_items=5, offset=-1):
                self.calls.append(("history", max_items, offset))
                return history

        client = ReadClient()
        with patch.object(server, "_client", return_value=client):
            session = await server.get_raw_workflow_session("tab-a")
            recent = await server.get_recent_history(max_items=99)

        self.assertIn("workflow", session)
        self.assertEqual(client.calls, [("session", "tab-a", True), ("history", 20, -1)])
        self.assertEqual(recent["history"], history)
        self.assertEqual(recent["history"]["prompt-7"]["status"]["messages"], history["prompt-7"]["status"]["messages"])

    async def test_unknown_third_party_schema_uses_live_object_info(self):
        choices = [f"choice-{index}" for index in range(225)]
        raw = {
            "display_name": "Custom Dynamic Node",
            "category": "custom/experimental",
            "description": "Runtime schema for a third-party node.",
            "python_module": "custom_nodes.example",
            "input": {
                "required": {"mode": [["fast", "quality"], {"default": "fast"}]},
                "optional": {"model": ["MODEL", {"tooltip": "Optional connection."}]},
                "hidden": {"unique_id": ["UNIQUE_ID", {}]},
            },
            "output": ["CUSTOM_TYPE"],
            "output_name": ["result"],
            "third_party_metadata": {"experimental_flag": True},
        }
        raw["input"]["required"]["preset"] = [choices, {"default": choices[0]}]
        client = FakeClient(raw)
        with patch.object(server, "_client", return_value=client):
            result = await server.get_node_schema("custom_nodes.example:Dynamic", True)

        self.assertEqual(
            client.calls,
            [("object_info", "custom_nodes.example:Dynamic")],
        )
        self.assertEqual(result["class_type"], "custom_nodes.example:Dynamic")
        self.assertEqual(result["inputs"]["optional"]["model"]["type"], "MODEL")
        enum = result["inputs"]["required"]["preset"]
        self.assertEqual(enum["choice_count"], 225)
        self.assertEqual(len(enum["choices"]), 200)
        self.assertTrue(enum["choices_truncated"])
        self.assertEqual(result["raw"], raw)
        self.assertEqual(result["raw"]["third_party_metadata"]["experimental_flag"], True)

    async def test_apply_returns_history_id_and_undo_forwards_expected_identity(self):
        client = FakeClient()
        with patch.object(server, "_client", return_value=client):
            applied = await server.apply_graph_operations(
                7, [{"op": "set_title", "node_id": "21", "title": "x"}], "tab-a"
            )
            undone = await server.undo_node_modification(
                "history-123", "21", 8, "tab-a"
            )

        self.assertEqual(applied["history_id"], "history-123")
        self.assertEqual(
            client.calls,
            [
                (
                    "command",
                    "apply_operations",
                    {
                        "operations": [
                            {"op": "set_title", "node_id": "21", "title": "x"}
                        ],
                        "reason": "",
                    },
                    {"session_id": "tab-a", "base_revision": 7, "timeout_ms": 20000},
                ),
                (
                    "command",
                    "undo_node",
                    {"history_id": "history-123", "node_id": "21"},
                    {"session_id": "tab-a", "base_revision": 8, "timeout_ms": 20000},
                ),
            ],
        )
        self.assertIn("history-123", str(undone))


if __name__ == "__main__":
    unittest.main()
