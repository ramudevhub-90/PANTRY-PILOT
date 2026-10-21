import copy
import json

import pytest
from starlette.testclient import TestClient

from pantry_pilot.app import create_app
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore


class ScriptedNova(MockNova):
    """Plays back canned Bedrock Converse responses and records what it was sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))  # the agent keeps appending to messages
        return {"output": {"message": self.responses.pop(0)}}


def tool_use(name, args, use_id):
    return {"role": "assistant", "content": [{"toolUse": {"toolUseId": use_id, "name": name, "input": args}}]}


def text(reply):
    return {"role": "assistant", "content": [{"text": reply}]}


def turn(client, body):
    with client.stream("POST", "/api/turn", json=body) as response:
        assert response.status_code == 200
        return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


@pytest.fixture
def store():
    return MemoryStore()


def test_agent_calls_mcp_tools_then_replies(store):
    nova = ScriptedNova([
        tool_use("get_inventory", {"filter": "expiring"}, "t1"),
        tool_use("plan_meals", {}, "t2"),
        text("<thinking>rice bowl uses the spinach</thinking>I'd make the Chicken & Spinach Rice Bowl. Want a rice timer?"),
    ])
    with TestClient(create_app(store, nova, "test")) as client:
        events = turn(client, {"text": "What can I make for dinner?"})

    kinds = [(e["type"], e.get("name")) for e in events]
    assert kinds == [
        ("tool_start", "get_inventory"), ("tool_end", "get_inventory"),
        ("tool_start", "plan_meals"), ("tool_end", "plan_meals"),
        ("reply", None), ("state", None),
    ]
    assert events[4]["text"] == "I'd make the Chicken & Spinach Rice Bowl. Want a rice timer?"
    assert events[5]["state"]["meals"][0]["id"] == "rice-bowl"

    # Nova saw the MCP tool list (minus scan_pantry) and got the tool results back.
    tools = {t["toolSpec"]["name"] for t in nova.calls[0]["tools"]}
    assert "plan_meals" in tools and "scan_pantry" not in tools
    tool_result = nova.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert tool_result["toolUseId"] == "t1" and "items" in tool_result["content"][0]["json"]


def test_agent_remembers_the_conversation(store):
    nova = ScriptedNova([
        text("Want a 15-minute rice timer?"),
        tool_use("set_timer", {"label": "rice", "minutes": 15}, "t1"),
        text("Okay, 15 minutes on the rice."),
    ])
    with TestClient(create_app(store, nova, "test")) as client:
        turn(client, {"text": "What's for dinner?"})
        events = turn(client, {"text": "yes"})

    assert events[-1]["state"]["timers"][0]["label"] == "rice"
    second_turn = nova.calls[1]["messages"]
    assert [m["role"] for m in second_turn] == ["user", "assistant", "user"]


def test_scan_and_confirm_flow(store):
    with TestClient(create_app(store, MockNova(), "test")) as client:
        events = turn(client, {"images": ["data:image/jpeg;base64,/9j/4AAQ"]})
        detections = next(e for e in events if e["type"] == "detections")["items"]
        assert events[0]["args"] == {"images": "1 photos", "kind": "auto"}

        tomatoes = next(d for d in detections if d["name"] == "Tomatoes") | {"quantity": 0.75}
        events = turn(client, {"confirmItems": [tomatoes]})

    inventory = {i["name"]: i for i in events[-1]["state"]["inventory"]}
    assert inventory["Tomatoes"]["quantity"] == 0.75
    assert inventory["Tomatoes"]["source"] == "scan"
    assert events[0]["name"] == "update_inventory"


def test_manual_add_to_pantry(store):
    with TestClient(create_app(store, MockNova(), "test")) as client:
        response = client.post("/api/pantry", json={"items": [
            {"name": "ghee", "quantity": 500, "unit": "ml", "category": "dairy", "emoji": "🧈", "expiresInDays": 90},
            {"name": "Eggs", "quantity": 6, "unit": "pcs"},
            {"name": "  ", "quantity": 1, "unit": "pcs"},
        ]})
    assert response.status_code == 200
    inventory = {i["name"]: i for i in response.json()["inventory"]}
    assert inventory["Ghee"]["quantity"] == 500 and inventory["Ghee"]["emoji"] == "🧈"
    assert inventory["Ghee"]["source"] == "manual"
    assert inventory["Eggs"]["quantity"] == 12  # added to the 6 already there


def test_chat_explains_when_nova_is_unavailable(store):
    with TestClient(create_app(store, MockNova(), "test")) as client:
        events = turn(client, {"text": "hello"})
    assert events[0]["type"] == "reply" and "Amazon Nova" in events[0]["text"]


def test_origin_secret_blocks_direct_calls(store, monkeypatch):
    monkeypatch.setenv("ORIGIN_SECRET", "s3cret")
    with TestClient(create_app(store, MockNova(), "test")) as client:
        assert client.get("/api/state").status_code == 403
        assert client.get("/api/state", headers={"x-origin-verify": "s3cret"}).status_code == 200
        assert client.get("/health").status_code == 200


def test_mcp_endpoint_serves_tools_over_streamable_http(store):
    with TestClient(create_app(store, MockNova(), "test"), base_url="http://localhost:8000") as client:
        response = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        )
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "pantry-pilot"


def test_conversation_survives_a_new_server_instance(store):
    first = ScriptedNova([text("Fajita tacos tonight. Start the timer?")])
    with TestClient(create_app(store, first, "alice")) as client:
        turn(client, {"text": "What's for dinner?"})

    # Lambda may route the follow-up to a different copy of the server.
    second = ScriptedNova([text("Timer started.")])
    with TestClient(create_app(store, second, "alice")) as client:
        turn(client, {"text": "Yes please"})

    sent = [m["content"][0]["text"] for m in second.calls[0]["messages"]]
    assert sent == ["What's for dinner?", "Fajita tacos tonight. Start the timer?", "Yes please"]


def test_old_conversation_starts_fresh(store, monkeypatch):
    with TestClient(create_app(store, ScriptedNova([text("Hi there.")]), "alice")) as client:
        turn(client, {"text": "Hello"})
    record = store.load_record("alice#chat")
    store.save_record("alice#chat", record | {"at": record["at"] - 31 * 60})

    nova = ScriptedNova([text("Hello again.")])
    with TestClient(create_app(store, nova, "alice")) as client:
        turn(client, {"text": "Hi"})
    assert [m["content"][0]["text"] for m in nova.calls[0]["messages"]] == ["Hi"]


def test_markdown_is_stripped_from_spoken_replies():
    from pantry_pilot.agent import plain

    assert plain("Top dinner pick: **Masoor Dal** with *rice*.") == "Top dinner pick: Masoor Dal with rice."
    assert plain("- Start the timer\n- `rice`") == "Start the timer\nrice"
    assert plain("2 * 3 = 6") == "2 * 3 = 6"


def test_language_requests_and_whats_for_dinner():
    from pantry_pilot.agent import requested_language, wants_meal_ideas

    assert requested_language("Alexa, talk to me in Bengali. What's for dinner tonight?") == "Bengali"
    assert requested_language("Switch back to English replies, please") == "English"
    assert requested_language("reply in Hindi") == "Hindi"
    assert requested_language("We're Bengali") is None  # home food, not a language switch
    assert wants_meal_ideas("Alexa, talk to me in Bengali. What's for dinner tonight?")


def test_same_language_as_i_speak():
    from pantry_pilot.agent import requested_language

    assert requested_language("Reply in the same language as me") == "Auto"
    assert requested_language("talk to me in whatever language I speak") == "Auto"
