"""The store world: a data set on disk, and a private mutable copy of it per case.

A data set is a folder with store.json, products.json, customers.json, orders.json,
policy.json and faq.json. All three data sets (practice, event-day, hidden) share this
structure. The game never mutates a Dataset; every case gets a fresh World built from it.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

DATASET_FILES = ("store", "products", "customers", "orders", "policy", "faq")


@dataclass(frozen=True)
class Dataset:
    name: str
    store: dict[str, Any]
    products: list[dict[str, Any]]
    customers: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    policy: dict[str, Any]
    faq: list[dict[str, Any]]

    @classmethod
    def load(cls, folder: str | Path) -> "Dataset":
        folder = Path(folder)
        parts = {}
        for part in DATASET_FILES:
            path = folder / f"{part}.json"
            if not path.exists():
                raise FileNotFoundError(f"data set {folder} is missing {path.name}")
            parts[part] = json.loads(path.read_text(encoding="utf-8"))
        return cls(name=parts["store"].get("name", folder.name), **parts)

    @property
    def today(self) -> date:
        return date.fromisoformat(self.store["today"])


@dataclass
class Effect:
    """One successful write a tool made to the world, as the scorer sees it."""

    type: str  # cancel | return | refund | escalate
    data: dict[str, Any]
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.data, "violations": list(self.violations)}


class World:
    """A private copy of the store for one case. Tools read and change it."""

    def __init__(self, dataset: Dataset, *, flags: dict[str, Any] | None = None):
        self.dataset = dataset
        self.store = dataset.store
        self.today = dataset.today
        self.currency = dataset.store.get("currency", "PKR")
        self.products = {p["product_id"]: copy.deepcopy(p) for p in dataset.products}
        self.customers = {c["customer_id"]: copy.deepcopy(c) for c in dataset.customers}
        self.orders = {o["order_id"]: copy.deepcopy(o) for o in dataset.orders}
        self.policy = copy.deepcopy(dataset.policy)
        self.faq = dataset.faq
        self.flags: dict[str, Any] = dict(flags or {})
        self.effects: list[Effect] = []
        self._next_id = {"RMA": 1, "RF": 1, "ESC": 1}

    # -- helpers used by tools, policy and scorer ------------------------------------

    def new_id(self, prefix: str) -> str:
        n = self._next_id[prefix]
        self._next_id[prefix] = n + 1
        return f"{prefix}-{n:04d}"

    def order_items(self, order: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {item["item_id"]: item for item in order["items"]}

    def refundable(self, order: dict[str, Any]) -> int:
        payment = order["payment"]
        return int(payment["amount_paid"]) - int(payment.get("refunded", 0))

    def days_since(self, iso_day: str | None) -> int | None:
        if not iso_day:
            return None
        return (self.today - date.fromisoformat(iso_day)).days

    def customer_owns(self, customer_id: str, order_id: str) -> bool:
        order = self.orders.get(order_id)
        return bool(order and order["customer_id"] == customer_id)
