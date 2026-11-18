#!/usr/bin/env python3
"""MCP smoke test: list tools, then run read-only calls if a device is attached."""

import asyncio
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[str(HERE / "server.py")],
)


async def main() -> None:
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.server_info.name, init.server_info.version)
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools ({len(names)}):", ", ".join(names))
            required = {"device_status", "adb_reboot", "fastboot_flash", "wait_for_device", "run_steps"}
            missing = required - set(names)
            if missing:
                raise SystemExit(f"missing tools: {sorted(missing)}")
            if "--no-device" in sys.argv:
                print("device calls skipped (--no-device)")
                return
            status = await session.call_tool("device_status", {})
            print("device_status:", status.content[0].text)
            log = await session.call_tool("adb_logcat", {"lines": 3})
            print("adb_logcat:", log.content[0].text[:400])


if __name__ == "__main__":
    asyncio.run(main())
