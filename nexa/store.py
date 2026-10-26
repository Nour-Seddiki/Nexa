"""Everything Nexa remembers: the profile, the cart, orders, bookings and what was spent.

One JSON file, written atomically. The spend ledger is the record the budget caps are
checked against, so it is appended in the same call that creates an order or a booking.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path

from . import config

DEFAULT_PROFILE = {
    "name": "",
    "city": "Paris",
    "email": "",
    "payment": "card ending 4242 (test card, nothing is really charged)",
    "notes": "",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.STATE_FILE)
        self.data = {
            "profile": dict(DEFAULT_PROFILE),
            "preferences": {},
            "cart": [],
            "orders": [],
            "bookings": [],
            "ledger": [],
        }
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in loaded.items():
                if key in self.data:
                    self.data[key] = value
            self.data["profile"] = {**DEFAULT_PROFILE, **self.data["profile"]}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # ---- profile and preferences -----------------------------------------

    @property
    def profile(self) -> dict:
        return self.data["profile"]

    @property
    def preferences(self) -> dict:
        return self.data["preferences"]

    def set_preference(self, key: str, value: str) -> None:
        key = key.strip().lower()
        if value is None or str(value).strip() == "":
            self.data["preferences"].pop(key, None)
        elif key in DEFAULT_PROFILE:
            self.data["profile"][key] = str(value).strip()
        else:
            self.data["preferences"][key] = str(value).strip()
        self.save()

    # ---- cart -------------------------------------------------------------

    @property
    def cart(self) -> list[dict]:
        return self.data["cart"]

    def add_line(self, product: dict, variant: dict, quantity: int) -> dict:
        for line in self.cart:
            if line["product_id"] == product["id"] and line["variant"] == variant:
                line["quantity"] += quantity
                self.save()
                return line
        line = {
            "line_id": _id("L"),
            "product_id": product["id"],
            "name": product["name"],
            "brand": product["brand"],
            "variant": variant,
            "quantity": quantity,
            "unit_price": product["price"],
            "added": _now(),
        }
        self.cart.append(line)
        self.save()
        return line

    def remove_line(self, line_id: str) -> dict | None:
        for i, line in enumerate(self.cart):
            if line["line_id"] == line_id or line["product_id"] == line_id:
                removed = self.cart.pop(i)
                self.save()
                return removed
        return None

    def set_quantity(self, line_id: str, quantity: int) -> dict | None:
        for line in self.cart:
            if line["line_id"] == line_id or line["product_id"] == line_id:
                if quantity <= 0:
                    return self.remove_line(line["line_id"])
                line["quantity"] = quantity
                self.save()
                return line
        return None

    def cart_subtotal(self) -> float:
        return round(sum(l["unit_price"] * l["quantity"] for l in self.cart), 2)

    def clear_cart(self) -> None:
        self.data["cart"] = []
        self.save()

    # ---- orders and bookings ---------------------------------------------

    @property
    def orders(self) -> list[dict]:
        return self.data["orders"]

    @property
    def bookings(self) -> list[dict]:
        return self.data["bookings"]

    def add_order(self, lines: list[dict], subtotal: float, shipping: float, total: float,
                  speed: str, eta: str, note: str = "") -> dict:
        order = {
            "id": _id("ORD"),
            "placed": _now(),
            "lines": [dict(l) for l in lines],
            "subtotal": subtotal,
            "shipping": shipping,
            "total": total,
            "shipping_speed": speed,
            "eta": eta,
            "note": note,
            "status": "placed",
        }
        self.orders.append(order)
        self._record(total, order["id"], f"order {order['id']}")
        self.save()
        return order

    def add_booking(self, kind: str, title: str, when: str, detail: dict, charged: float,
                    cancel_policy: str = "") -> dict:
        booking = {
            "id": _id(kind[:3].upper()),
            "kind": kind,
            "title": title,
            "when": when,
            "detail": detail,
            "charged": charged,
            "cancel_policy": cancel_policy,
            "booked": _now(),
            "status": "confirmed",
        }
        self.bookings.append(booking)
        if charged:
            self._record(charged, booking["id"], f"{kind} {booking['id']}")
        self.save()
        return booking

    def find_booking(self, booking_id: str) -> dict | None:
        for b in self.bookings:
            if b["id"].lower() == str(booking_id).strip().lower():
                return b
        return None

    def find_order(self, order_id: str) -> dict | None:
        for o in self.orders:
            if o["id"].lower() == str(order_id).strip().lower():
                return o
        return None

    def cancel_booking(self, booking_id: str, refund: float = 0.0) -> dict | None:
        booking = self.find_booking(booking_id)
        if booking is None or booking["status"] == "cancelled":
            return booking
        booking["status"] = "cancelled"
        booking["cancelled"] = _now()
        booking["refund"] = refund
        if refund:
            self._record(-refund, booking["id"], f"refund {booking['id']}")
        self.save()
        return booking

    def cancel_order(self, order_id: str) -> dict | None:
        order = self.find_order(order_id)
        if order is None or order["status"] == "cancelled":
            return order
        order["status"] = "cancelled"
        order["cancelled"] = _now()
        self._record(-order["total"], order["id"], f"refund {order['id']}")
        self.save()
        return order

    # ---- spend ------------------------------------------------------------

    def _record(self, amount: float, ref: str, what: str) -> None:
        self.data["ledger"].append(
            {"date": date.today().isoformat(), "amount": round(amount, 2), "ref": ref, "what": what}
        )

    def spent_today(self) -> float:
        today = date.today().isoformat()
        return round(sum(e["amount"] for e in self.data["ledger"] if e["date"] == today), 2)

    def budget_left_today(self) -> float:
        return round(config.DAILY_CAP - self.spent_today(), 2)
