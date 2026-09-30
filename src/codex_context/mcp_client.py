from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from .models import ContextError


class MCPClient:
    """Small stdio JSON-RPC diagnostic client; no model or API key required."""

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
                 timeout: float = 45) -> None:
        self.timeout = timeout
        self.sequence = 0
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.stderr: list[str] = []
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                        start_new_session=os.name != "nt",
                                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
                if isinstance(message, dict):
                    self.messages.put(message)
            except ValueError:
                continue
        self.messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line.rstrip())
            del self.stderr[:-12]

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ContextError("MCP process closed its input") from exc

    def request(self, method: str, params: dict[str, Any]) -> Any:
        import time
        self.sequence += 1
        identifier = self.sequence
        self.send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise ContextError(f"MCP timeout during {method}; " + " | ".join(self.stderr[-3:])) from exc
            if message is None:
                raise ContextError(f"MCP exited during {method}; " + " | ".join(self.stderr[-3:]))
            if message.get("id") == identifier:
                if "error" in message:
                    raise ContextError(f"MCP {method}: {message['error']}")
                return message.get("result", {})
            if "method" in message and "id" in message:
                if message["method"] == "ping":
                    self.send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                else:
                    self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Unsupported diagnostic callback"}})
            if time.monotonic() >= deadline:
                raise ContextError(f"MCP timeout during {method}")

    def initialize(self) -> dict[str, Any]:
        result: dict[str, Any] = self.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                           "clientInfo": {"name": "codex-context", "version": "0.1.0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def tools(self) -> dict[str, Any]:
        result = self.request("tools/list", {})
        return {tool["name"]: tool for tool in result.get("tools", [])}

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise ContextError(f"MCP tool {name} failed: {result.get('content')}")
        for item in result.get("content", []):
            if item.get("type") == "text" and item.get("text", "").lstrip().startswith("Error"):
                raise ContextError(f"MCP tool {name}: {item['text']}")
        return result

    def close(self) -> None:
        if self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
            else:
                getattr(os, "killpg")(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    getattr(os, "killpg")(self.process.pid, getattr(signal, "SIGKILL"))
                else:
                    self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                stream.close()

    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
