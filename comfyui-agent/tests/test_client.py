from __future__ import annotations

import sys
from pathlib import Path
import unittest

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server" / "src"))

from comfyui_agent_bridge_mcp.client import ComfyBridgeClient  # noqa: E402


class ClientConfigurationTests(unittest.TestCase):
    def test_normalizes_api_suffix(self):
        client = ComfyBridgeClient("http://127.0.0.1:8188/api/")
        self.assertEqual(client.base_url, "http://127.0.0.1:8188")
        self.assertEqual(client.api_base, "http://127.0.0.1:8188/api")

    def test_preserves_reverse_proxy_path(self):
        client = ComfyBridgeClient("https://example.invalid/comfy")
        self.assertEqual(client.api_base, "https://example.invalid/comfy/api")

    def test_rejects_relative_url(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            ComfyBridgeClient("127.0.0.1:8188")


class RuntimeSchemaRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_session_requests_full_workflow_and_view(self):
        session = {
            "session_id": "tab:one",
            "workflow": {"nodes": [{"id": 1, "type": "CustomNode"}]},
            "view": {"nodes": [{"id": 1, "comfy_class": "CustomNode"}]},
        }
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"session": session})

        client = ComfyBridgeClient()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            result = await client.session("tab:one", raw=True)
        finally:
            await client.close()

        self.assertEqual(
            requests[0].url.raw_path,
            b"/api/comfy-agent-bridge/v1/sessions/tab%3Aone?raw=1",
        )
        self.assertEqual(result, session)

    async def test_exact_custom_node_schema_is_fetched_from_object_info(self):
        class_type = "vendor.custom:UnknownNode"
        schema = {
            "display_name": "Unknown Node",
            "input": {
                "required": {"choice": [["a", "b"], {}]},
                "optional": {"anything": ["*", {}]},
            },
            "future_field": {"third_party": True},
        }
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={class_type: schema})

        client = ComfyBridgeClient()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            result = await client.object_info(class_type)
        finally:
            await client.close()

        self.assertEqual(requests[0].url.raw_path, b"/api/object_info/vendor.custom%3AUnknownNode")
        self.assertEqual(result[class_type], schema)

    async def test_recent_history_and_bridge_errors_keep_raw_payloads(self):
        history = {
            "prompt-7": {
                "status": {
                    "status_str": "error",
                    "completed": False,
                    "messages": [["execution_error", {"exception_message": "raw failure"}]],
                }
            }
        }
        bridge_errors = {
            "ok": True,
            "count": 1,
            "limit": 100,
            "errors": [{"code": "browser_error", "message": "raw command failure"}],
        }
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.path == "/api/history":
                return httpx.Response(200, json=history)
            if request.url.path == "/api/comfy-agent-bridge/v1/errors":
                return httpx.Response(200, json=bridge_errors)
            return httpx.Response(404, json={"error": "unexpected path"})

        client = ComfyBridgeClient()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            recent = await client.recent_history(max_items=50)
            errors = await client.recent_errors()
        finally:
            await client.close()

        self.assertEqual(dict(requests[0].url.params), {"max_items": "20", "offset": "-1"})
        self.assertEqual(requests[1].url.path, "/api/comfy-agent-bridge/v1/errors")
        self.assertEqual(recent, history)
        self.assertEqual(recent["prompt-7"]["status"]["messages"], history["prompt-7"]["status"]["messages"])
        self.assertEqual(errors, bridge_errors)


if __name__ == "__main__":
    unittest.main()
