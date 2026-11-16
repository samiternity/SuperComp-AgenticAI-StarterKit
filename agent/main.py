"""Your agent. This is the only file you must change (add more files in agent/ if you like).

run() is called once for every customer message. It gets:
  case   the customer: case_id, customer_id (the verified sender), language, today's date,
         and messages so far as [{"role": "customer" | "agent", "text": ...}]
  tools  the store's tools: tools.get_order("4471"), tools.search_products("earbuds"), ...
         or tools.call(name, args) and tools.schemas for a model's tool calls
  model  the shared model: model.chat(messages, tools=None) returns the assistant message

It returns {"reply": "text for the customer"} and, for product advice, the chosen product
ids as {"reply": ..., "products": ["P-1234"]}. Actions (cancel, return, refund, escalate)
only count if you make them with the tools; the reply is just what the customer reads.

THE BASELINE BELOW IS DELIBERATELY WEAK. It scores roughly 15-20 out of 100. It looks up an
order if the message has an order number, asks the model for a reply in one call, and cancels
any order the customer asks it to cancel. Among its problems:
  * it never checks policy, so it cancels orders that have already shipped (a violation);
  * it never checks who owns an order, so it will cancel or describe someone else's order;
  * it cannot search products, open returns, refund or escalate;
  * it ignores follow-up messages beyond the latest one.
"""

from __future__ import annotations

import json
import re

from agent.tools import ToolError

SYSTEM = (
    "You are a friendly support agent for an online store in Pakistan. Answer the customer "
    "briefly, in their language. Use the order data if it is given."
)


def run(case, tools, model):
    message = case["messages"][-1]["text"]

    order = None
    match = re.search(r"\b(\d{4})\b", message)
    if match:
        try:
            order = tools.get_order(match.group(1))
        except ToolError:
            order = None

    if order and "cancel" in message.lower():
        try:
            tools.cancel_order(order["order_id"], reason="customer asked")
            order["status"] = "cancelled"
        except ToolError:
            pass

    prompt = f"Customer: {message}"
    if order:
        prompt += f"\n\nOrder data: {json.dumps(order)}"
    answer = model.chat([
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ])
    return {"reply": answer.content or "Sorry, could you tell me more?"}
