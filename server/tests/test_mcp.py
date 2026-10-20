"""End to end tests for onrecord.mcp_server over stdio.

The server is driven as a real subprocess (sys.executable -m
onrecord.mcp_server) with JSON-RPC lines piped on stdin, DATA_DIR
pointed at a tmp directory, and ONRECORD_VECTOR=fake so no ML or
network dependency is involved. Fixtures are local to this file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = [
    "list_people",
    "list_interactions_with_person",
    "search_conversations",
    "get_interaction",
    "get_person_profile",
]


def run_mcp(tmp_path, raw_lines, timeout=60):
    """Feed raw lines to a fresh MCP subprocess and parse its replies.

    raw_lines are written verbatim (so tests can include a malformed
    line). Returns (parsed_responses, completed_process).
    """
    env = dict(os.environ)
    env["DATA_DIR"] = str(tmp_path)
    env["ONRECORD_VECTOR"] = "fake"
    env["CHROMA_DIR"] = str(Path(tmp_path) / "chroma")
    # Isolate from any ambient database or webhook configuration.
    env.pop("DATABASE_URL", None)
    env.pop("SLACK_WEBHOOK_URL", None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "onrecord.mcp_server"],
        cwd=str(SERVER_DIR),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = "".join(
        line if line.endswith("\n") else line + "\n" for line in raw_lines
    )
    try:
        stdout, stderr = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise AssertionError(
            "mcp_server did not exit within timeout. stdout="
            + stdout
            + " stderr="
            + stderr
        )
    responses = []
    for line in stdout.splitlines():
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses, proc, stderr


def rpc(req_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def tool_call(req_id, name, arguments=None):
    return rpc(
        req_id,
        "tools/call",
        {"name": name, "arguments": arguments or {}},
    )


def by_id(responses, req_id):
    for response in responses:
        if response.get("id") == req_id:
            return response
    raise AssertionError("no response with id " + str(req_id) + ": " + repr(responses))


def text_payload(result):
    content = result["content"]
    assert content and content[0]["type"] == "text"
    return content[0]["text"]


# ----------------------------------------------------------------------
# Protocol basics
# ----------------------------------------------------------------------


def test_initialize_returns_insight_mcp(tmp_path):
    responses, proc, _stderr = run_mcp(
        tmp_path, [rpc(1, "initialize", {"protocolVersion": "2024-11-05"})]
    )
    assert proc.returncode == 0
    response = by_id(responses, 1)
    assert "error" not in response
    assert response["result"]["serverInfo"]["name"] == "insight-mcp"


def test_tools_list_returns_exactly_five_tools(tmp_path):
    responses, proc, _stderr = run_mcp(tmp_path, [rpc(1, "tools/list")])
    assert proc.returncode == 0
    response = by_id(responses, 1)
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == EXPECTED_TOOLS
    assert len(names) == 5


# ----------------------------------------------------------------------
# Tools on an empty DB
# ----------------------------------------------------------------------


def test_list_people_empty_db_returns_empty_list(tmp_path):
    responses, proc, _stderr = run_mcp(
        tmp_path, [tool_call(1, "list_people")]
    )
    assert proc.returncode == 0
    result = by_id(responses, 1)["result"]
    assert not result.get("isError")
    payload = json.loads(text_payload(result))
    assert payload == []


def test_search_conversations_empty_db_is_empty_no_people(tmp_path):
    responses, proc, _stderr = run_mcp(
        tmp_path,
        [tool_call(1, "search_conversations", {"query": "budget"})],
    )
    assert proc.returncode == 0
    result = by_id(responses, 1)["result"]
    assert not result.get("isError")
    text = text_payload(result)
    assert "EMPTY" in text
    assert "NO_PEOPLE" in text
    payload = json.loads(text)
    assert payload["status"] == "EMPTY"
    assert payload["reason"] == "NO_PEOPLE"


def test_get_person_profile_unknown_person_is_error(tmp_path):
    responses, proc, _stderr = run_mcp(
        tmp_path,
        [tool_call(1, "get_person_profile", {"person": "no-such-person-xyz"})],
    )
    assert proc.returncode == 0
    result = by_id(responses, 1)["result"]
    assert result.get("isError") is True


# ----------------------------------------------------------------------
# Robustness
# ----------------------------------------------------------------------


def test_malformed_json_line_does_not_kill_process(tmp_path):
    responses, proc, _stderr = run_mcp(
        tmp_path,
        [
            "{this is not valid json",
            rpc(7, "initialize", {}),
            rpc(8, "tools/list"),
        ],
    )
    assert proc.returncode == 0
    init = by_id(responses, 7)
    assert init["result"]["serverInfo"]["name"] == "insight-mcp"
    listed = by_id(responses, 8)
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == EXPECTED_TOOLS
