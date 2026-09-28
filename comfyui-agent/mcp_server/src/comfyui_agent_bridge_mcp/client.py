from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx


class BridgeHttpError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class ComfyBridgeClient:
    def __init__(self, base_url: str | None = None) -> None:
        configured = base_url or os.environ.get("COMFYUI_URL") or "http://127.0.0.1:8188"
        parts = urlsplit(configured.strip())
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("COMFYUI_URL must be an absolute http:// or https:// URL")
        if parts.query or parts.fragment:
            raise ValueError("COMFYUI_URL must not contain a query string or fragment")
        path = parts.path.rstrip("/")
        if path.endswith("/api"):
            path = path[:-4]
        self.base_url = urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")
        self.api_base = f"{self.base_url}/api"
        self._http: httpx.AsyncClient | None = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
                headers={"User-Agent": "ComfyUI-Agent-Bridge-MCP/1.3"},
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float = 20.0,
        **kwargs: Any,
    ) -> Any:
        try:
            response = await self._http_client().request(
                method,
                f"{self.api_base}{path}",
                timeout=timeout,
                **kwargs,
            )
        except httpx.RequestError as error:
            raise BridgeHttpError(
                0,
                "comfyui_unreachable",
                f"cannot reach {self.base_url}: {error}",
            ) from error
        try:
            data = response.json()
        except ValueError as error:
            snippet = response.text[:300]
            raise BridgeHttpError(
                response.status_code,
                "invalid_json",
                f"ComfyUI returned non-JSON data: {snippet}",
            ) from error
        if response.is_error:
            raw_error = data.get("error") if isinstance(data, dict) else None
            if isinstance(raw_error, dict):
                code = str(raw_error.get("code") or f"http_{response.status_code}")
                message = str(raw_error.get("message") or response.reason_phrase)
                details = {key: value for key, value in raw_error.items() if key not in {"code", "message"}}
            else:
                code = f"http_{response.status_code}"
                message = str(raw_error or response.reason_phrase)
                details = {}
            raise BridgeHttpError(response.status_code, code, message, details)
        return data

    async def bridge_status(self) -> dict[str, Any]:
        return await self._request("GET", "/comfy-agent-bridge/v1/status")

    async def recent_errors(self) -> dict[str, Any]:
        data = await self._request("GET", "/comfy-agent-bridge/v1/errors")
        return data if isinstance(data, dict) else {}

    async def sessions(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/comfy-agent-bridge/v1/sessions")
        return data.get("sessions", [])

    async def session(self, session_id: str | None = None, raw: bool = False) -> dict[str, Any]:
        if not session_id:
            sessions = await self.sessions()
            online = [item for item in sessions if item.get("online")]
            if len(online) == 1:
                session_id = str(online[0]["session_id"])
            else:
                status = await self.bridge_status()
                session_id = status.get("active_session_id")
            if not session_id:
                if not online:
                    raise BridgeHttpError(
                        404,
                        "session_not_found",
                        "no live ComfyUI browser session; open the ComfyUI page and wait for the Agent Bridge badge",
                    )
                raise BridgeHttpError(
                    409,
                    "session_ambiguous",
                    "multiple browser sessions are active; call list_live_sessions and pass session_id",
                )
        encoded = quote(session_id, safe="")
        data = await self._request(
            "GET",
            f"/comfy-agent-bridge/v1/sessions/{encoded}?raw={'1' if raw else '0'}",
        )
        return data["session"]

    async def command(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        base_revision: int | None = None,
        timeout_ms: int = 15000,
    ) -> dict[str, Any]:
        body = {
            "session_id": session_id,
            "action": action,
            "payload": payload,
            "timeout_ms": timeout_ms,
        }
        if base_revision is not None:
            body["base_revision"] = base_revision
        return await self._request(
            "POST",
            "/comfy-agent-bridge/v1/command",
            json=body,
            timeout=max(20.0, timeout_ms / 1000 + 5.0),
        )

    async def object_info(self, class_type: str | None = None) -> dict[str, Any]:
        path = "/object_info"
        if class_type:
            path = f"{path}/{quote(class_type, safe='')}"
        data = await self._request("GET", path, timeout=45.0)
        return data if isinstance(data, dict) else {}

    async def models(self, folder: str) -> list[str]:
        data = await self._request("GET", f"/models/{quote(folder, safe='')}", timeout=45.0)
        return data if isinstance(data, list) else []

    async def history(self, prompt_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/history/{quote(prompt_id, safe='')}", timeout=45.0)
        return data if isinstance(data, dict) else {}

    async def recent_history(self, max_items: int = 5, offset: int = -1) -> dict[str, Any]:
        params = {
            "max_items": max(1, min(int(max_items), 20)),
            "offset": max(-1, int(offset)),
        }
        data = await self._request("GET", "/history", params=params, timeout=45.0)
        return data if isinstance(data, dict) else {}

    async def queue(self) -> dict[str, Any]:
        data = await self._request("GET", "/queue")
        return data if isinstance(data, dict) else {}

    async def system_stats(self) -> dict[str, Any]:
        data = await self._request("GET", "/system_stats")
        return data if isinstance(data, dict) else {}

    def view_url(self, item: dict[str, Any]) -> str | None:
        filename = item.get("filename")
        if not isinstance(filename, str) or not filename:
            return None
        query = urlencode(
            {
                "filename": filename,
                "subfolder": item.get("subfolder", ""),
                "type": item.get("type", "output"),
            }
        )
        return f"{self.api_base}/view?{query}"

    async def wait_for_history(
        self,
        prompt_id: str,
        *,
        timeout_seconds: int = 300,
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        poll_seconds = max(0.2, min(float(poll_seconds), 5.0))
        while True:
            history = await self.history(prompt_id)
            if prompt_id in history:
                return history[prompt_id]
            if loop.time() >= deadline:
                raise BridgeHttpError(
                    408,
                    "generation_timeout",
                    f"prompt {prompt_id} did not finish within {timeout_seconds} seconds",
                )
            await asyncio.sleep(poll_seconds)
