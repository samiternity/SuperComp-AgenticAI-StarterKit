"""Step 1 for first-time agent builders: a plain tool-calling loop.

This is the basic pattern behind almost every AI agent:

    1. Send the model the conversation plus the list of tools it may use.
    2. If the model asks for tools, run them and append each result to the conversation.
    3. Repeat until the model answers in plain text (or you run out of calls).
    4. Return that answer as the reply.

Try it without touching your own agent:

    python run_local.py --agent examples.tool_loop_agent --sample 5 --trace --view

It is a starting point, not a winning agent. It knows nothing about the store's policies, it
does not check who owns an order before changing it, and it never returns product ids. Those
are your next steps; read the failure reasons with --trace to see which matter most. When you
are ready, copy what you need into agent/main.py: only the agent/ folder is judged.
"""

from __future__ import annotations

import json
from typing import Any

from agent.model import MAX_CALLS_PER_CASE, ModelLimitError

SYSTEM = """You are the customer support agent of {store}, an online electronics and home store.
Today is {today}. Prices are in {currency}. The customer writing to you is {customer_id}.

Use the tools to look things up and to take actions; writing that you did something does not
do it. Keep the final reply short (two or three sentences), in the customer's language
({language}), and include the facts they asked for, such as order numbers, dates and amounts."""

# Each model call may return at most 150 tokens. A long tool call can be cut off mid-JSON,
# and the model can run out of calls, so leave one call for writing the reply.
CALLS_FOR_TOOLS = MAX_CALLS_PER_CASE - 1


def run(case: dict[str, Any], tools, model) -> dict[str, Any]:
    # 1. The conversation so far: the system prompt, then every customer and agent message.
    #    Multi-turn cases call run() again with the longer history, so replay all of it.
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM.format(
        store=case.get("store", "the store"), today=case["today"],
        currency=case.get("currency", "PKR"), customer_id=case["customer_id"],
        language=case["language"])}]
    for m in case["messages"]:
        role = "user" if m["role"] == "customer" else "assistant"
        messages.append({"role": role, "content": m["text"]})

    try:
        for _ in range(CALLS_FOR_TOOLS):
            # 2. Ask the model what to do next, offering it the store's tools.
            answer = model.chat(messages, tools=tools.schemas)
            if not answer.tool_calls:
                # 3. No tool calls means the model has written its reply.
                return {"reply": answer.content or "Sorry, could you tell me a bit more?"}

            # The model's own message (with its tool calls) must go into the history first,
            # then one "tool" message per call, matched by tool_call_id.
            messages.append({
                "role": "assistant",
                "content": answer.content or "",
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name,
                                             "arguments": c.function.arguments}}
                               for c in answer.tool_calls],
            })
            for call in answer.tool_calls:
                # tools.call never raises. Arguments cut off at 150 tokens come back as
                # {"ok": False, "error": "arguments are not valid JSON"}, and the model sees it.
                result = tools.call(call.function.name, call.function.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})

        # 4. Out of tool rounds: ask for a reply with no tools offered.
        messages.append({"role": "user", "content": "Now write your reply to the customer."})
        answer = model.chat(messages)
        return {"reply": answer.content or "Sorry, could you tell me a bit more?"}
    except ModelLimitError:
        return {"reply": "Sorry, I could not finish looking into this. A colleague will follow up."}
