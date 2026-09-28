from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT.parent / "comfyui-plugin"
ROUTES = {}


class _FakeRoutes:
    def get(self, path):
        return self._register("GET", path)

    def post(self, path):
        return self._register("POST", path)

    @staticmethod
    def _register(method, path):
        def register(handler):
            ROUTES[(method, path)] = handler
            return handler

        return register


class _FakeRequest:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")
        self.content_length = len(self._body)

    async def read(self):
        return self._body


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.text = json.dumps(payload)


class _FakeWeb:
    Request = object
    Response = _FakeResponse

    @staticmethod
    def json_response(payload, status=200):
        return _FakeResponse(payload, status)


def _load_routes_module():
    package_name = "bridge_routes_test_plugin"
    package = types.ModuleType(package_name)
    package.__path__ = [str(PLUGIN)]
    server = types.ModuleType("server")
    server.PromptServer = type(
        "PromptServer",
        (),
        {"instance": types.SimpleNamespace(routes=_FakeRoutes())},
    )
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_user_directory = tempfile.gettempdir
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.web = _FakeWeb
    sentry = object()
    old_server = sys.modules.get("server", sentry)
    old_folder_paths = sys.modules.get("folder_paths", sentry)
    old_aiohttp = sys.modules.get("aiohttp", sentry)
    sys.modules[package_name] = package
    sys.modules["server"] = server
    sys.modules["folder_paths"] = folder_paths
    sys.modules["aiohttp"] = aiohttp
    try:
        return importlib.import_module(f"{package_name}.bridge_routes")
    finally:
        for name, old in (
            ("server", old_server),
            ("folder_paths", old_folder_paths),
            ("aiohttp", old_aiohttp),
        ):
            if old is sentry:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


bridge_routes = _load_routes_module()


class BridgeRouteErrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bridge_routes.RECENT_ERRORS.clear()
        bridge_routes.PENDING.clear()

    async def test_failed_command_result_is_readable_from_errors_endpoint(self):
        pending = bridge_routes.PendingCommand(
            "command-1",
            "session-1",
            "apply_operations",
            asyncio.get_running_loop().create_future(),
            time.time(),
        )
        bridge_routes.PENDING[pending.command_id] = pending
        response = await bridge_routes.command_result(
            _FakeRequest(
                {
                    "command_id": pending.command_id,
                    "session_id": pending.session_id,
                    "ok": False,
                    "error": {
                        "code": "unknown_node",
                        "message": "Custom node was not found",
                        "details": {"node_type": "ExampleCustomNode"},
                    },
                }
            )
        )
        self.assertEqual(response.status, 200)

        response = await bridge_routes.recent_errors(None)
        payload = json.loads(response.text)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(
            payload["errors"][0]["command_id"], pending.command_id
        )
        self.assertEqual(payload["errors"][0]["session_id"], pending.session_id)
        self.assertEqual(payload["errors"][0]["action"], "apply_operations")
        self.assertIsInstance(payload["errors"][0]["timestamp"], (int, float))
        self.assertEqual(payload["errors"][0]["code"], "unknown_node")
        self.assertEqual(
            payload["errors"][0]["details"], {"node_type": "ExampleCustomNode"}
        )
        self.assertFalse(payload["errors"][0]["truncated"])

        status = json.loads((await bridge_routes.bridge_status(None)).text)
        self.assertEqual(status["recent_error_count"], 1)

    async def test_error_history_is_bounded_and_marks_truncation(self):
        for index in range(bridge_routes.MAX_RECENT_ERRORS + 2):
            pending = bridge_routes.PendingCommand(
                f"command-{index}", "session-1", "queue_prompt", None, time.time()
            )
            bridge_routes._record_command_error(
                pending,
                {
                    "code": "e" * (bridge_routes.MAX_ERROR_CODE_CHARS + 1),
                    "message": "m" * (bridge_routes.MAX_ERROR_MESSAGE_CHARS + 1),
                    "details": {"payload": "d" * bridge_routes.MAX_ERROR_DETAILS_CHARS},
                },
            )

        payload = json.loads((await bridge_routes.recent_errors(None)).text)
        self.assertEqual(payload["count"], bridge_routes.MAX_RECENT_ERRORS)
        newest_index = bridge_routes.MAX_RECENT_ERRORS + 1
        self.assertEqual(
            payload["errors"][0]["command_id"], f"command-{newest_index}"
        )
        self.assertEqual(payload["errors"][-1]["command_id"], "command-2")
        self.assertTrue(payload["errors"][0]["truncated"])
        self.assertEqual(
            len(payload["errors"][0]["code"]),
            bridge_routes.MAX_ERROR_CODE_CHARS,
        )
        self.assertEqual(
            len(payload["errors"][0]["message"]),
            bridge_routes.MAX_ERROR_MESSAGE_CHARS,
        )
        self.assertLessEqual(
            len(payload["errors"][0]["details"]["preview"]),
            bridge_routes.MAX_ERROR_DETAILS_CHARS,
        )


if __name__ == "__main__":
    unittest.main()
