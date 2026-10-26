"""The merchant side: catalogue, restaurant tables, hotel rooms, flights.

This is the seam a real integration would replace. Everything Nexa can see about the
world comes from here, and every price the agent is allowed to quote is computed here -
never taken from the model. The shipped implementation reads JSON fixtures and derives
availability and fares deterministically from the request, so the same question always
gets the same answer and the test suite needs no network.

To plug in a real provider, keep the method names and return shapes and swap the body.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

CABIN_DEFAULT = "economy"


class MarketError(ValueError):
    """A request that the merchant side cannot make sense of."""


# ---- small helpers --------------------------------------------------------

def parse_date(text: str, what: str = "date") -> date:
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise MarketError(f"{what} must look like 2026-11-04, got {text!r}") from None


def _seed(*parts: object) -> int:
    key = "|".join(str(p) for p in parts)
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)


def _pick(lo: int, hi: int, *parts: object) -> int:
    """Stable integer in [lo, hi] for these parts."""
    return lo + _seed(*parts) % (hi - lo + 1)


def _tokens(text: str) -> set[str]:
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(t) > 1}


class Market:
    def __init__(self, fixtures: Path | None = None):
        root = Path(fixtures or config.FIXTURES)
        self.products = _load(root / "catalog.json")["products"]
        places = _load(root / "places.json")
        self.restaurants = places["restaurants"]
        self.hotels = places["hotels"]
        air = _load(root / "flights.json")
        self.flights = air["flights"]
        self.airports = air["airports"]
        self.city_airports = air["city_airports"]
        self.cabins = air["cabins"]

    # ---- products ---------------------------------------------------------

    def get_product(self, product_id: str) -> dict:
        for p in self.products:
            if p["id"] == product_id:
                return p
        raise MarketError(f"no product with id {product_id!r}")

    def search_products(self, query: str, *, max_price: float | None = None,
                        min_rating: float | None = None, category: str | None = None,
                        in_stock_only: bool = True, limit: int = 6) -> list[dict]:
        want = _tokens(query or "")
        hits = []
        for p in self.products:
            if max_price is not None and p["price"] > float(max_price):
                continue
            if min_rating is not None and p["rating"] < float(min_rating):
                continue
            if category and category.lower() not in p["category"].lower():
                continue
            if in_stock_only and p["stock"] <= 0:
                continue
            haystack = _tokens(f"{p['name']} {p['brand']} {p['category']} {p['description']}")
            overlap = len(want & haystack)
            if want and not overlap:
                continue
            hits.append((overlap, p["rating"], -p["price"], p))
        hits.sort(key=lambda h: h[:3], reverse=True)
        return [h[3] for h in hits[:limit]]

    def check_variant(self, product: dict, variant: dict | None) -> dict:
        """Validate a chosen variant, filling in the only option where there is one."""
        chosen = dict(variant or {})
        for axis, options in (product.get("variants") or {}).items():
            value = chosen.get(axis)
            if value is None:
                if len(options) == 1:
                    chosen[axis] = options[0]
                    continue
                raise MarketError(
                    f"{product['name']} needs a {axis}: {', '.join(map(str, options))}")
            if str(value) not in [str(o) for o in options]:
                raise MarketError(
                    f"{value!r} is not an available {axis} for {product['name']} "
                    f"({', '.join(map(str, options))})")
            chosen[axis] = str(value)
        unknown = set(chosen) - set(product.get("variants") or {})
        if unknown:
            raise MarketError(f"{product['name']} has no option called {', '.join(sorted(unknown))}")
        return chosen

    def shipping_cost(self, subtotal: float, speed: str = "standard") -> float:
        if speed not in ("standard", "express"):
            raise MarketError("shipping speed must be 'standard' or 'express'")
        if speed == "express":
            return 12.90
        return 0.0 if subtotal >= 60 else 4.90

    # ---- restaurants ------------------------------------------------------

    def get_restaurant(self, restaurant_id: str) -> dict:
        for r in self.restaurants:
            if r["id"] == restaurant_id:
                return r
        raise MarketError(f"no restaurant with id {restaurant_id!r}")

    def search_restaurants(self, city: str, *, cuisine: str | None = None, party_size: int = 2,
                           when: str | None = None, max_price_level: int | None = None,
                           limit: int = 6) -> list[dict]:
        day = parse_date(when, "date") if when else None
        out = []
        for r in self.restaurants:
            if city and city.lower() not in r["city"].lower():
                continue
            if cuisine and cuisine.lower() not in r["cuisine"].lower():
                continue
            if max_price_level is not None and r["price_level"] > int(max_price_level):
                continue
            if party_size > r["party_max"]:
                continue
            free = self.restaurant_slots(r["id"], day, party_size) if day else list(r["slots"])
            if day and not free:
                continue
            out.append(dict(r, free_slots=free))
        out.sort(key=lambda r: r["rating"], reverse=True)
        return out[:limit]

    def restaurant_slots(self, restaurant_id: str, day: date, party_size: int) -> list[str]:
        """Which of the restaurant's slots still have a table for this party, on this day."""
        r = self.get_restaurant(restaurant_id)
        if day.weekday() not in r["open_days"]:
            return []
        if party_size > r["party_max"]:
            return []
        free = []
        for slot in r["slots"]:
            tables = _pick(0, 6, "table", r["id"], day.isoformat(), slot)
            seats = tables * 2 + _pick(0, 3, "seats", r["id"], day.isoformat(), slot)
            if seats >= party_size:
                free.append(slot)
        return free

    def restaurant_deposit(self, restaurant: dict, party_size: int) -> float:
        per_person = float(restaurant.get("deposit_per_person") or 0.0)
        if not per_person:
            return 0.0
        if restaurant["id"] == "r-bcn-01" and party_size < 4:
            return 0.0
        return round(per_person * party_size, 2)

    # ---- hotels -----------------------------------------------------------

    def get_hotel(self, hotel_id: str) -> dict:
        for h in self.hotels:
            if h["id"] == hotel_id:
                return h
        raise MarketError(f"no hotel with id {hotel_id!r}")

    def search_hotels(self, city: str, check_in: str, check_out: str, *, guests: int = 1,
                      max_nightly: float | None = None, limit: int = 6) -> list[dict]:
        quotes = []
        for h in self.hotels:
            if city and city.lower() not in h["city"].lower():
                continue
            room = "double" if guests > 1 else "single"
            if room not in h["rooms"]:
                room = sorted(h["rooms"])[0]
            try:
                q = self.quote_hotel(h["id"], check_in, check_out, guests=guests, room_type=room)
            except MarketError:
                continue
            if not q["available"]:
                continue
            if max_nightly is not None and q["avg_nightly"] > float(max_nightly):
                continue
            quotes.append(q)
        quotes.sort(key=lambda q: q["avg_nightly"])
        return quotes[:limit]

    def quote_hotel(self, hotel_id: str, check_in: str, check_out: str, *, guests: int = 1,
                    room_type: str = "double") -> dict:
        h = self.get_hotel(hotel_id)
        start, end = parse_date(check_in, "check_in"), parse_date(check_out, "check_out")
        nights = (end - start).days
        if nights < 1:
            raise MarketError("check_out must be at least one night after check_in")
        if nights > 30:
            raise MarketError("this agent books at most 30 nights in one booking")
        if room_type not in h["rooms"]:
            raise MarketError(
                f"{h['name']} has no {room_type} room ({', '.join(sorted(h['rooms']))})")
        cap = {"single": 1, "double": 2, "suite": 4}.get(room_type, 2)
        if guests > cap:
            raise MarketError(f"a {room_type} room at {h['name']} sleeps {cap}")
        per_night, available = [], True
        for i in range(nights):
            night = start + timedelta(days=i)
            rate = h["nightly"] * h["rooms"][room_type]
            if night.weekday() in (4, 5):
                rate += h["weekend_surcharge"]
            rooms_left = _pick(0, 5, "room", h["id"], room_type, night.isoformat())
            if rooms_left == 0:
                available = False
            per_night.append({"date": night.isoformat(), "rate": round(rate, 2), "rooms_left": rooms_left})
        total = round(sum(n["rate"] for n in per_night), 2)
        return {
            "hotel": h, "hotel_id": h["id"], "name": h["name"], "city": h["city"],
            "stars": h["stars"], "rating": h["rating"], "address": h["address"], "note": h["note"],
            "check_in": start.isoformat(), "check_out": end.isoformat(), "nights": nights,
            "room_type": room_type, "guests": guests, "per_night": per_night,
            "avg_nightly": round(total / nights, 2), "total": total,
            "breakfast_per_person": h["breakfast"], "available": available,
            "free_cancel_until": (start - timedelta(days=h["refundable_until_days"])).isoformat(),
        }

    # ---- flights ----------------------------------------------------------

    def get_flight(self, flight_id: str) -> dict:
        for f in self.flights:
            if f["id"] == flight_id:
                return f
        raise MarketError(f"no flight with id {flight_id!r}")

    def resolve_airports(self, place: str) -> list[str]:
        key = (place or "").strip()
        if key.upper() in self.airports:
            return [key.upper()]
        codes = self.city_airports.get(key.lower())
        if not codes:
            known = ", ".join(sorted(self.city_airports))
            raise MarketError(f"unknown place {place!r}. Known cities: {known}")
        return codes

    def search_flights(self, origin: str, destination: str, when: str, *, passengers: int = 1,
                       cabin: str = CABIN_DEFAULT, max_price: float | None = None,
                       nonstop_only: bool = False, limit: int = 6) -> list[dict]:
        froms, tos = self.resolve_airports(origin), self.resolve_airports(destination)
        day = parse_date(when, "date")
        out = []
        for f in self.flights:
            if f["origin"] not in froms or f["destination"] not in tos:
                continue
            if nonstop_only and f["stops"]:
                continue
            q = self.quote_flight(f["id"], when, passengers=passengers, cabin=cabin)
            if not q["available"]:
                continue
            if max_price is not None and q["total"] > float(max_price):
                continue
            out.append(q)
        if not out:
            return []
        out.sort(key=lambda q: (q["total"], q["duration_min"]))
        _ = day
        return out[:limit]

    def quote_flight(self, flight_id: str, when: str, *, passengers: int = 1,
                     cabin: str = CABIN_DEFAULT) -> dict:
        f = self.get_flight(flight_id)
        day = parse_date(when, "date")
        cabin = (cabin or CABIN_DEFAULT).lower()
        if cabin not in self.cabins:
            raise MarketError(f"cabin must be one of {', '.join(self.cabins)}")
        if passengers < 1 or passengers > 9:
            raise MarketError("passengers must be between 1 and 9")
        today = date.today()
        if day < today:
            raise MarketError(f"{when} is in the past")
        lead = (day - today).days
        lead_factor = 1.8 if lead < 3 else 1.45 if lead < 7 else 1.2 if lead < 14 else 1.0 if lead < 30 else 0.9
        day_factor = {4: 1.18, 6: 1.15, 1: 0.92, 2: 0.92}.get(day.weekday(), 1.0)
        noise = 1 + (_pick(0, 18, "fare", f["id"], day.isoformat()) - 9) / 100
        fare = round(f["base_price"] * lead_factor * day_factor * self.cabins[cabin] * noise, 2)
        seats = _pick(0, 9, "seat", f["id"], day.isoformat(), cabin)
        return {
            "flight": f, "flight_id": f["id"], "airline": f["airline"], "number": f["number"],
            "origin": f["origin"], "destination": f["destination"],
            "origin_name": self.airports[f["origin"]], "destination_name": self.airports[f["destination"]],
            "date": day.isoformat(), "weekday": day.strftime("%a"), "dep": f["dep"], "arr": f["arr"],
            "duration_min": f["duration_min"], "stops": f["stops"], "bags": f["bags"],
            "cabin": cabin, "passengers": passengers, "fare_per_passenger": fare,
            "total": round(fare * passengers, 2), "seats_left": seats,
            "available": bool(day.weekday() in f["days"] and seats >= passengers),
            "reason": "" if day.weekday() in f["days"] else f"{f['number']} does not fly on {day.strftime('%A')}",
        }


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MarketError(f"missing fixture {path}") from None
