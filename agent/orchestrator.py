import os
import json
import httpx
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

N8N_BASE_URL = os.environ.get("N8N_BASE_URL", "")
N8N_AUTH_TOKEN = os.environ.get("N8N_AUTH_TOKEN", "")
N8N_AVAILABLE_FLOWS = [
    f.strip()
    for f in os.environ.get("N8N_AVAILABLE_FLOWS", "").split(",")
    if f.strip()
]

TOOLS = [
    {
        "name": "list_available_flows",
        "description": "Returns the list of n8n flows available to trigger.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "trigger_n8n_flow",
        "description": "Triggers an n8n flow via webhook and returns its response.",
        "input_schema": {
            "type": "object",
            "properties": {
                "flow_name": {
                    "type": "string",
                    "description": "The name of the flow to trigger (must be in the available list).",
                },
                "payload": {
                    "type": "object",
                    "description": "Optional JSON payload to send to the webhook.",
                },
            },
            "required": ["flow_name"],
        },
    },
]


def list_available_flows() -> dict:
    return {"flows": N8N_AVAILABLE_FLOWS}


def trigger_n8n_flow(flow_name: str, payload: dict | None = None) -> dict:
    if flow_name not in N8N_AVAILABLE_FLOWS:
        return {"error": f"Flow '{flow_name}' not found. Available: {N8N_AVAILABLE_FLOWS}"}

    url = f"{N8N_BASE_URL}/webhook/{flow_name}"
    headers = {"Content-Type": "application/json"}
    if N8N_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {N8N_AUTH_TOKEN}"

    try:
        response = httpx.post(url, json=payload or {}, headers=headers, timeout=30)
        response.raise_for_status()
        return {"status": response.status_code, "body": response.json()}
    except httpx.HTTPStatusError as e:
        # Avoid leaking response body (may contain sensitive data) into logs
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": type(e).__name__}


def dispatch_tool(name: str, tool_input: dict) -> str:
    if name == "list_available_flows":
        result = list_available_flows()
    elif name == "trigger_n8n_flow":
        result = trigger_n8n_flow(
            flow_name=tool_input["flow_name"],
            payload=tool_input.get("payload"),
        )
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)


MAX_TOOL_ROUNDS = 20


def run(task: str) -> str:
    print("[orchestrator] starting task")
    messages = [{"role": "user", "content": task}]
    rounds = 0

    while rounds < MAX_TOOL_ROUNDS:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            tools=TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            rounds += 1
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"[orchestrator] tool call: {block.name}")
                    output = dispatch_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    if rounds >= MAX_TOOL_ROUNDS:
        return "Error: max tool rounds reached."
    return ""


if __name__ == "__main__":
    task = os.environ.get("TASK", "List all available flows and trigger each one.")
    final = run(task)
    print("\n[orchestrator] final answer:")
    print(final)
