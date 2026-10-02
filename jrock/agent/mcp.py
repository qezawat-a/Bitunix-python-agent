"""Minimal MCP client: stdio (JSON-RPC over subprocess) and streamable HTTP."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx

from .context import AgentContext

FILE = Path(__file__).resolve().parents[2] / "data" / "mcp" / "servers.json"


class MCPServer:
    def __init__(self, name: str, spec: dict) -> None:
        self.name = name
        self.spec = spec
        self.proc: asyncio.subprocess.Process | None = None
        self.tools: list[dict] = []
        self._id = 0
        self._lock = asyncio.Lock()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _stdin_send(self, payload: dict) -> dict:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(payload) + "\n").encode())
        await self.proc.stdin.drain()
        assert self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed the connection")
            msg = json.loads(line)
            if msg.get("id") == payload.get("id"):
                return msg

    async def _http_send(self, payload: dict) -> dict:
        url = self.spec["url"]
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.spec.get("api_key"):
            headers["Authorization"] = f"Bearer {self.spec['api_key']}"
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.text
        if data.startswith("event:") or data.startswith("data:"):
            for line in data.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
        return r.json()

    async def send(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method,
                   "params": params or {}}
        async with self._lock:
            if self.spec.get("url"):
                return await self._http_send(payload)
            return await self._stdin_send(payload)

    async def start(self) -> None:
        if self.spec.get("url"):
            return
        env = dict(os.environ, **(self.spec.get("env") or {}))
        self.proc = await asyncio.create_subprocess_exec(
            self.spec["command"], *(self.spec.get("args") or []),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env)
        await self.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "jrock", "version": "1.0"},
        })
        await self.notify("notifications/initialized")
        res = await self.send("tools/list")
        self.tools = res.get("result", {}).get("tools", [])

    async def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if self.spec.get("url"):
            await self._http_send(payload)
            return
        if self.proc and self.proc.stdin:
            async with self._lock:
                self.proc.stdin.write((json.dumps(payload) + "\n").encode())
                await self.proc.stdin.drain()

    async def call(self, tool: str, args: dict) -> str:
        res = await self.send("tools/call", {"name": tool, "arguments": args})
        if "error" in res:
            return f"MCP error: {res['error']}"
        out = []
        for block in res.get("result", {}).get("content", []):
            out.append(block.get("text", json.dumps(block)))
        return "\n".join(out) or json.dumps(res.get("result", {}))[:4000]

    async def stop(self) -> None:
        if self.proc:
            self.proc.kill()
            self.proc = None


class MCPManager:
    def __init__(self) -> None:
        self.servers: dict[str, MCPServer] = {}
        self.load()

    def load(self) -> None:
        if FILE.exists():
            try:
                specs = json.loads(FILE.read_text())
            except Exception:
                return
            for name, spec in specs.items():
                self.servers[name] = MCPServer(name, spec)

    def save(self) -> None:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(
            {n: s.spec for n, s in self.servers.items()}, indent=2))

    def add(self, name: str, spec: dict) -> None:
        self.servers[name] = MCPServer(name, spec)
        self.save()

    def remove(self, name: str) -> bool:
        if name in self.servers:
            del self.servers[name]
            self.save()
            return True
        return False

    async def connect_all(self) -> str:
        ok, fail = [], []
        for s in self.servers.values():
            try:
                await s.start()
                ok.append(f"{s.name} ({len(s.tools)} tools)")
            except Exception as e:
                fail.append(f"{s.name}: {e}")
        lines = []
        if ok:
            lines.append("Connected: " + ", ".join(ok))
        if fail:
            lines.append("Failed: " + "; ".join(fail))
        return "\n".join(lines) or "No MCP servers configured."

    def tool_specs(self) -> list[dict]:
        specs = []
        for s in self.servers.values():
            for t in s.tools:
                specs.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{s.name}__{t['name']}",
                        "description": t.get("description", "")[:400],
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                })
        return specs

    async def call(self, full_name: str, args: dict, ctx: AgentContext) -> str:
        _, server_name, tool = full_name.split("__", 2)
        server = self.servers.get(server_name)
        if not server:
            return f"MCP server '{server_name}' not found."
        return await server.call(tool, args)
