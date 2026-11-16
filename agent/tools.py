"""The store's tools as Python functions. Generated from the game; do not edit.

Each function calls the game with this run's token and returns the tool's result, or raises
ToolError with the game's message. tools.call(name, args) does the same for a tool chosen by
name (handy for a model's tool calls) and never raises: it returns {"ok": ..., "result" or
"error": ...}. tools.schemas is the list of tool definitions in the OpenAI format.

The game's address and the run token come from the environment (GAME_URL, RUN_TOKEN), so the
same code works on a laptop and on the server.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


class ToolError(Exception):
    """The game refused the call: a missing id, a bad argument, or a limit reached."""


class Tools:
    def __init__(self, game_url: str | None = None, run_token: str | None = None,
                 on_call=None):
        self.game_url = (game_url or os.environ["GAME_URL"]).rstrip("/")
        self.run_token = run_token or os.environ["RUN_TOKEN"]
        self._http = httpx.Client(timeout=30)
        self.on_call = on_call  # optional callback(name, args, outcome), e.g. for logging
        self.calls = 0

    def call(self, name: str, args: dict[str, Any] | str | None = None) -> dict[str, Any]:
        """Runs a tool by name. args may be a dict or a JSON string. Never raises."""
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                return {"ok": False, "error": "arguments are not valid JSON"}
        self.calls += 1
        try:
            res = self._http.post(f"{self.game_url}/store/{name}", json=args or {},
                                  headers={"Authorization": f"Bearer {self.run_token}"})
            outcome = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            outcome = {"ok": False, "error": f"could not reach the game: {exc}"}
        if "ok" not in outcome:
            outcome = {"ok": False, "error": outcome.get("error", str(outcome))}
        if self.on_call:
            self.on_call(name, args, outcome)
        return outcome

    def _run(self, name: str, **args: Any) -> Any:
        outcome = self.call(name, {k: v for k, v in args.items() if v is not None})
        if not outcome["ok"]:
            raise ToolError(outcome["error"])
        return outcome["result"]

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return SCHEMAS

    def search_products(self, query: str, max_price: float | None = None, category: str | None = None) -> Any:
        """Search the catalogue by keywords. Returns up to 8 matching products with id, name, category, price and whether they are in stock. Optionally filter by maximum price and by category. Use get_product for full specifications.
            query: Keywords, e.g. 'wireless earbuds noise cancelling'
            max_price: Only products at or below this price
            category: Exact category id, e.g. 'earbuds'
        """
        return self._run("search_products", query=query, max_price=max_price, category=category)

    def get_product(self, product_id: str) -> Any:
        """Full details of one product: price, specifications, stock count, warranty, and whether it is final sale.
            product_id: e.g. 'P-1023'
        """
        return self._run("get_product", product_id=product_id)

    def get_customer(self, customer_id: str) -> Any:
        """A customer's profile: name, email, phone, address and membership date.
            customer_id: e.g. 'C-102'
        """
        return self._run("get_customer", customer_id=customer_id)

    def list_orders(self, customer_id: str) -> Any:
        """A customer's orders, newest first, with status, order date, total and item names.
            customer_id: e.g. 'C-102'
        """
        return self._run("list_orders", customer_id=customer_id)

    def get_order(self, order_id: str) -> Any:
        """Full details of one order: owner, status, items with item ids and prices, order, ship and delivery dates, expected delivery, payment, refunds and returns.
            order_id: e.g. '4471'
        """
        return self._run("get_order", order_id=order_id)

    def get_policy(self, topic: str) -> Any:
        """The store's current policy text on one topic. Policies can change, so read them rather than relying on memory.
            topic: Policy topic; one of cancellation, returns, refunds, privacy, shipping, escalation
        """
        return self._run("get_policy", topic=topic)

    def search_faq(self, query: str) -> Any:
        """Search the store's FAQ. Returns up to 3 matching questions with answers.
            query: Keywords or a question
        """
        return self._run("search_faq", query=query)

    def cancel_order(self, order_id: str, reason: str) -> Any:
        """Cancel a whole order. Prepaid orders are refunded automatically when cancelled.
            order_id
            reason: Short reason given by the customer
        """
        return self._run("cancel_order", order_id=order_id, reason=reason)

    def create_return(self, order_id: str, item_id: str, reason: str) -> Any:
        """Open a return for one item of an order. A return also covers a replacement.
            order_id
            item_id: Item id from get_order, e.g. '4471-2'
            reason: one of damaged, defective, wrong_item, changed_mind
        """
        return self._run("create_return", order_id=order_id, item_id=item_id, reason=reason)

    def issue_refund(self, order_id: str, amount: float) -> Any:
        """Refund money to the customer for an order, to the original payment method.
            order_id
            amount: Amount in the store's currency
        """
        return self._run("issue_refund", order_id=order_id, amount=amount)

    def escalate(self, summary: str) -> Any:
        """Hand the case to a human agent with a short summary of what the customer needs.
            summary
        """
        return self._run("escalate", summary=summary)


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search the catalogue by keywords. Returns up to 8 matching products with id, name, category, price and whether they are in stock. Optionally filter by maximum price and by category. Use get_product for full specifications.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords, e.g. 'wireless earbuds noise cancelling'"
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Only products at or below this price"
                    },
                    "category": {
                        "type": "string",
                        "description": "Exact category id, e.g. 'earbuds'"
                    }
                },
                "required": [
                    "query"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Full details of one product: price, specifications, stock count, warranty, and whether it is final sale.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "e.g. 'P-1023'"
                    }
                },
                "required": [
                    "product_id"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": "A customer's profile: name, email, phone, address and membership date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "e.g. 'C-102'"
                    }
                },
                "required": [
                    "customer_id"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_orders",
            "description": "A customer's orders, newest first, with status, order date, total and item names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "e.g. 'C-102'"
                    }
                },
                "required": [
                    "customer_id"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Full details of one order: owner, status, items with item ids and prices, order, ship and delivery dates, expected delivery, payment, refunds and returns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "e.g. '4471'"
                    }
                },
                "required": [
                    "order_id"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_policy",
            "description": "The store's current policy text on one topic. Policies can change, so read them rather than relying on memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": [
                            "cancellation",
                            "returns",
                            "refunds",
                            "privacy",
                            "shipping",
                            "escalation"
                        ],
                        "description": "Policy topic"
                    }
                },
                "required": [
                    "topic"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_faq",
            "description": "Search the store's FAQ. Returns up to 3 matching questions with answers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or a question"
                    }
                },
                "required": [
                    "query"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel a whole order. Prepaid orders are refunded automatically when cancelled.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short reason given by the customer"
                    }
                },
                "required": [
                    "order_id",
                    "reason"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_return",
            "description": "Open a return for one item of an order. A return also covers a replacement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string"
                    },
                    "item_id": {
                        "type": "string",
                        "description": "Item id from get_order, e.g. '4471-2'"
                    },
                    "reason": {
                        "type": "string",
                        "enum": [
                            "damaged",
                            "defective",
                            "wrong_item",
                            "changed_mind"
                        ]
                    }
                },
                "required": [
                    "order_id",
                    "item_id",
                    "reason"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": "Refund money to the customer for an order, to the original payment method.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string"
                    },
                    "amount": {
                        "type": "number",
                        "description": "Amount in the store's currency"
                    }
                },
                "required": [
                    "order_id",
                    "amount"
                ],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalate",
            "description": "Hand the case to a human agent with a short summary of what the customer needs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string"
                    }
                },
                "required": [
                    "summary"
                ],
                "additionalProperties": False
            }
        }
    }
]
