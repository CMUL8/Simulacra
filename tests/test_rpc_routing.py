from __future__ import annotations

import asyncio

import pytest

from simulacra.rpc import RpcClient


@pytest.mark.asyncio
async def test_handle_line_routes_response_and_events() -> None:
	client = RpcClient()
	events: list[dict] = []
	client.on_event(lambda e: events.append(e))

	loop = asyncio.get_running_loop()
	fut: asyncio.Future[dict] = loop.create_future()
	client._pending["req_1"] = fut

	await client._handle_line('{"type":"response","id":"req_1","command":"prompt","success":true}')
	assert fut.done()
	assert fut.result()["success"] is True

	await client._handle_line('{"type":"agent_start"}')
	assert events == [{"type": "agent_start"}]
