"""The eleven store tools. This registry is the single source of truth for their names,
descriptions and argument schemas: the agent-facing API, the starter kit's tools.py and the
model's function definitions are all generated from it.

Tools do not enforce policy. A write performs its action whenever its target exists; the
policy checks run alongside and are recorded on the write's Effect for the scorer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from . import policy
from .world import Effect, World


class ToolError(Exception):
    """A plain error returned to the agent, such as a missing id or a bad argument."""


@dataclass
class ToolContext:
    world: World
    verified_customer_id: str


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str  # read | write
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        """The OpenAI-compatible function definition for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TOOLS: dict[str, Tool] = {}


def _params(required: list[str], **props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def tool(name: str, kind: str, description: str, parameters: dict[str, Any]):
    def register(fn):
        TOOLS[name] = Tool(name, kind, description, parameters, fn)
        return fn

    return register


# -- argument checking -----------------------------------------------------------------

_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


def _check_args(t: Tool, args: Any) -> dict[str, Any]:
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be a JSON object")
    props = t.parameters["properties"]
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolError(f"unknown argument(s) {unknown}; {t.name} takes {sorted(props)}")
    for name in t.parameters["required"]:
        if args.get(name) in (None, ""):
            raise ToolError(f"missing required argument '{name}'")
    clean = {}
    for name, value in args.items():
        if value is None:
            continue
        spec = props[name]
        expected = _JSON_TYPES[spec["type"]]
        if spec["type"] in ("integer", "number") and isinstance(value, str):
            try:
                value = float(value.replace(",", "")) if spec["type"] == "number" else int(value)
            except ValueError:
                raise ToolError(f"'{name}' must be a {spec['type']}") from None
        if isinstance(value, bool) and spec["type"] != "boolean":
            raise ToolError(f"'{name}' must be a {spec['type']}")
        if not isinstance(value, expected):
            raise ToolError(f"'{name}' must be a {spec['type']}")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"'{name}' must be one of {spec['enum']}")
        clean[name] = value
    return clean


def call_tool(ctx: ToolContext, name: str, args: Any) -> Any:
    """Run one tool. Returns its result, or raises ToolError with a message for the agent."""
    t = TOOLS.get(name)
    if t is None:
        raise ToolError(f"unknown tool '{name}'; tools are {sorted(TOOLS)}")
    return t.fn(ctx, **_check_args(t, args))


# -- shared helpers --------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(str(text).lower())


def _get_order(ctx: ToolContext, order_id: str) -> dict[str, Any]:
    order_id = str(order_id).strip().lstrip("#")
    order = ctx.world.orders.get(order_id)
    if order is None:
        raise ToolError(f"no order with id '{order_id}'")
    return order


def _product_summary(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": p["product_id"],
        "name": p["name"],
        "category": p["category"],
        "price": p["price"],
        "in_stock": p["stock"] > 0,
        "rating": p.get("rating"),
    }


def _order_summary(ctx: ToolContext, o: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": o["order_id"],
        "status": o["status"],
        "placed_on": o["placed_on"],
        "total": o["total"],
        "items": [item["name"] for item in o["items"]],
    }


# -- read tools ------------------------------------------------------------------------


@tool(
    "search_products",
    "read",
    "Search the catalogue by keywords. Returns up to 8 matching products with id, name, "
    "category, price and whether they are in stock. Optionally filter by maximum price and "
    "by category. Use get_product for full specifications.",
    _params(
        ["query"],
        query={"type": "string", "description": "Keywords, e.g. 'wireless earbuds noise cancelling'"},
        max_price={"type": "number", "description": "Only products at or below this price"},
        category={"type": "string", "description": "Exact category id, e.g. 'earbuds'"},
    ),
)
def search_products(ctx: ToolContext, query: str, max_price: float | None = None,
                    category: str | None = None):
    world = ctx.world
    if category is not None:
        categories = sorted({p["category"] for p in world.products.values()})
        if category not in categories:
            raise ToolError(f"unknown category '{category}'; categories are {categories}")
    words = _tokens(query)
    scored = []
    for p in world.products.values():
        if category is not None and p["category"] != category:
            continue
        if max_price is not None and p["price"] > max_price:
            continue
        name = set(_tokens(p["name"]) + _tokens(p.get("brand", "")))
        tagged = set(_tokens(p["category"]) + [t for tag in p.get("tags", []) for t in _tokens(tag)])
        rest = set(_tokens(p.get("description", "")))
        for key, value in p.get("specs", {}).items():
            rest.update(_tokens(key.replace("_", " ")))
            rest.update(_tokens(" ".join(value) if isinstance(value, list) else value))
        score = sum(3 if w in name else 2 if w in tagged else 1 if w in rest else 0 for w in words)
        if words and score == 0:
            continue
        scored.append((-score, p["price"], p))
    scored.sort(key=lambda s: (s[0], s[1]))
    return {
        "results": [_product_summary(p) for _, _, p in scored[:8]],
        "total_matches": len(scored),
    }


@tool(
    "get_product",
    "read",
    "Full details of one product: price, specifications, stock count, warranty, and whether "
    "it is final sale.",
    _params(["product_id"], product_id={"type": "string", "description": "e.g. 'P-1023'"}),
)
def get_product(ctx: ToolContext, product_id: str):
    p = ctx.world.products.get(product_id.strip())
    if p is None:
        raise ToolError(f"no product with id '{product_id}'")
    return {
        **_product_summary(p),
        "brand": p.get("brand"),
        "description": p.get("description"),
        "specs": p.get("specs", {}),
        "stock": p["stock"],
        "warranty_months": p.get("warranty_months", 0),
        "final_sale": p.get("final_sale", False),
        "final_sale_reason": p.get("final_sale_reason"),
    }


@tool(
    "get_customer",
    "read",
    "A customer's profile: name, email, phone, address and membership date.",
    _params(["customer_id"], customer_id={"type": "string", "description": "e.g. 'C-102'"}),
)
def get_customer(ctx: ToolContext, customer_id: str):
    c = ctx.world.customers.get(customer_id.strip())
    if c is None:
        raise ToolError(f"no customer with id '{customer_id}'")
    return dict(c)


@tool(
    "list_orders",
    "read",
    "A customer's orders, newest first, with status, order date, total and item names.",
    _params(["customer_id"], customer_id={"type": "string", "description": "e.g. 'C-102'"}),
)
def list_orders(ctx: ToolContext, customer_id: str):
    customer_id = customer_id.strip()
    if customer_id not in ctx.world.customers:
        raise ToolError(f"no customer with id '{customer_id}'")
    orders = [o for o in ctx.world.orders.values() if o["customer_id"] == customer_id]
    orders.sort(key=lambda o: o["placed_on"], reverse=True)
    return {"orders": [_order_summary(ctx, o) for o in orders]}


@tool(
    "get_order",
    "read",
    "Full details of one order: owner, status, items with item ids and prices, order, ship "
    "and delivery dates, expected delivery, payment, refunds and returns.",
    _params(["order_id"], order_id={"type": "string", "description": "e.g. '4471'"}),
)
def get_order(ctx: ToolContext, order_id: str):
    o = _get_order(ctx, order_id)
    return {key: value for key, value in o.items()}


@tool(
    "get_policy",
    "read",
    "The store's current policy text on one topic. Policies can change, so read them rather "
    "than relying on memory.",
    _params(
        ["topic"],
        topic={"type": "string", "enum": list(policy.POLICY_TOPICS),
               "description": "Policy topic"},
    ),
)
def get_policy(ctx: ToolContext, topic: str):
    return {"topic": topic, "text": policy.policy_text(ctx.world, topic)}


@tool(
    "search_faq",
    "read",
    "Search the store's FAQ. Returns up to 3 matching questions with answers.",
    _params(["query"], query={"type": "string", "description": "Keywords or a question"}),
)
def search_faq(ctx: ToolContext, query: str):
    words = set(_tokens(query))
    ranked = []
    for entry in ctx.world.faq:
        overlap = len(words & set(_tokens(entry["question"] + " " + entry["answer"])))
        if overlap:
            ranked.append((-overlap, entry))
    ranked.sort(key=lambda r: r[0])
    return {"results": [entry for _, entry in ranked[:3]]}


# -- write tools -----------------------------------------------------------------------


@tool(
    "cancel_order",
    "write",
    "Cancel a whole order. Prepaid orders are refunded automatically when cancelled.",
    _params(
        ["order_id", "reason"],
        order_id={"type": "string"},
        reason={"type": "string", "description": "Short reason given by the customer"},
    ),
)
def cancel_order(ctx: ToolContext, order_id: str, reason: str):
    world = ctx.world
    order = _get_order(ctx, order_id)
    if order["status"] == "cancelled":
        raise ToolError(f"order {order['order_id']} is already cancelled")
    violations = policy.check_cancel(world, ctx.verified_customer_id, order)
    order["status"] = "cancelled"
    order["cancelled_on"] = world.today.isoformat()
    auto_refund = world.refundable(order)
    if auto_refund > 0:
        order["payment"]["refunded"] = int(order["payment"].get("refunded", 0)) + auto_refund
        order.setdefault("refunds", []).append(
            {"refund_id": world.new_id("RF"), "amount": auto_refund, "source": "cancellation"}
        )
    world.effects.append(Effect("cancel", {"order_id": order["order_id"]}, violations))
    return {"order_id": order["order_id"], "status": "cancelled", "automatic_refund": auto_refund}


@tool(
    "create_return",
    "write",
    "Open a return for one item of an order. A return also covers a replacement.",
    _params(
        ["order_id", "item_id", "reason"],
        order_id={"type": "string"},
        item_id={"type": "string", "description": "Item id from get_order, e.g. '4471-2'"},
        reason={"type": "string", "enum": list(policy.RETURN_REASONS)},
    ),
)
def create_return(ctx: ToolContext, order_id: str, item_id: str, reason: str):
    world = ctx.world
    order = _get_order(ctx, order_id)
    item = world.order_items(order).get(item_id.strip())
    if item is None:
        raise ToolError(
            f"order {order['order_id']} has no item '{item_id}'; its items are "
            f"{sorted(world.order_items(order))}"
        )
    for ret in order.get("returns", []):
        if ret["item_id"] == item["item_id"] and ret["status"] == "open":
            raise ToolError(f"item {item['item_id']} already has open return {ret['return_id']}")
    violations = policy.check_return(world, ctx.verified_customer_id, order, item, reason)
    ret = {"return_id": world.new_id("RMA"), "item_id": item["item_id"], "reason": reason,
           "status": "open", "opened_on": world.today.isoformat()}
    order.setdefault("returns", []).append(ret)
    world.effects.append(Effect(
        "return",
        {"order_id": order["order_id"], "item_id": item["item_id"], "reason": reason,
         "return_id": ret["return_id"]},
        violations,
    ))
    return {"return_id": ret["return_id"], "status": "open"}


@tool(
    "issue_refund",
    "write",
    "Refund money to the customer for an order, to the original payment method.",
    _params(
        ["order_id", "amount"],
        order_id={"type": "string"},
        amount={"type": "number", "description": "Amount in the store's currency"},
    ),
)
def issue_refund(ctx: ToolContext, order_id: str, amount: float):
    world = ctx.world
    order = _get_order(ctx, order_id)
    if world.flags.get("payment_outage"):
        raise ToolError("the payment system is unavailable; refunds cannot be issued right now")
    amount = int(round(amount))
    if amount <= 0:
        raise ToolError("amount must be greater than zero")
    if amount > world.refundable(order):
        raise ToolError(
            f"amount {amount} is more than the {world.refundable(order)} still refundable on "
            f"order {order['order_id']}"
        )
    violations = policy.check_refund(world, ctx.verified_customer_id, order, amount)
    refund = {"refund_id": world.new_id("RF"), "amount": amount, "source": "agent"}
    order["payment"]["refunded"] = int(order["payment"].get("refunded", 0)) + amount
    order.setdefault("refunds", []).append(refund)
    world.effects.append(Effect(
        "refund", {"order_id": order["order_id"], "amount": amount,
                   "refund_id": refund["refund_id"]}, violations,
    ))
    return {"refund_id": refund["refund_id"], "amount": amount}


@tool(
    "escalate",
    "write",
    "Hand the case to a human agent with a short summary of what the customer needs.",
    _params(["summary"], summary={"type": "string"}),
)
def escalate(ctx: ToolContext, summary: str):
    if len(summary) > 1000:
        raise ToolError("summary must be at most 1000 characters")
    ticket = ctx.world.new_id("ESC")
    ctx.world.effects.append(Effect("escalate", {"summary": summary, "ticket_id": ticket}))
    return {"ticket_id": ticket, "status": "escalated"}


def tool_schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in TOOLS.values()]
