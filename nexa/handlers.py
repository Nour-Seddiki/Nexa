"""What each tool actually does. One function per tool, all returning a string.

Failures come back as `ERROR: ...` rather than raising, so the model reads what went
wrong and fixes its next call. The six tools in `tools.CONFIRM` re-price their receipt
here and refuse if it no longer matches what the human approved.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from . import config
from .market import MarketError, parse_date
from .tools import CONFIRM, Context, Receipt, ToolError, _fence, _product_line, _variants, preview


def dispatch(name: str, args: dict, ctx: Context, receipt: Receipt | None = None) -> str:
    fn = _HANDLERS.get(name)
    if fn is None:
        return f"ERROR: no tool called {name!r}"
    try:
        if name in CONFIRM:
            fresh = preview(name, args, ctx)
            if receipt is not None and (round(fresh.total, 2) != round(receipt.total, 2)
                                        or round(fresh.refund, 2) != round(receipt.refund, 2)):
                return ("ERROR: the price changed between the confirmation and the charge "
                        f"({config.money(receipt.total)} -> {config.money(fresh.total)}). "
                        "Nothing was bought. Show the new price and ask again.")
            return fn(args, ctx, fresh)
        return fn(args, ctx)
    except (ToolError, MarketError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, KeyError):
            return f"ERROR: {name} needs the {exc} argument"
        return f"ERROR: {exc}"


# ---- looking around --------------------------------------------------------

def _today(args: dict, ctx: Context) -> str:
    today = date.today()
    days = [f"{(today + timedelta(days=i)).strftime('%A')} {(today + timedelta(days=i)).isoformat()}"
            for i in range(8)]
    return "Today is " + days[0] + ". Next: " + "; ".join(days[1:])


def _get_profile(args: dict, ctx: Context) -> str:
    p, store = ctx.store.profile, ctx.store
    prefs = "; ".join(f"{k}={v}" for k, v in store.preferences.items()) or "none saved yet"
    return (f"name: {p['name'] or 'unknown'}\ncity: {p['city']}\npayment: {p['payment']}\n"
            f"preferences: {prefs}\n"
            f"budget: {config.money(store.budget_left_today())} left of today's "
            f"{config.money(config.DAILY_CAP)}; single purchase limit "
            f"{config.money(config.MAX_ORDER)}\n"
            f"cart: {len(store.cart)} line(s), {config.money(store.cart_subtotal())}")


def _set_preference(args: dict, ctx: Context) -> str:
    key = str(args["key"]).strip()
    value = str(args.get("value", "")).strip()
    ctx.store.set_preference(key, value)
    return f"saved {key}={value}" if value else f"forgot {key}"


def _search_products(args: dict, ctx: Context) -> str:
    hits = ctx.market.search_products(
        args.get("query", ""), max_price=args.get("max_price"), min_rating=args.get("min_rating"),
        category=args.get("category"))
    if not hits:
        return "No product matches. Try fewer words, a higher max_price, or a different category."
    body = "\n".join(_product_line(p) for p in hits)
    return f"{len(hits)} match(es):\n" + _fence(body)


def _get_product(args: dict, ctx: Context) -> str:
    p = ctx.market.get_product(args["product_id"])
    body = "\n".join([
        _product_line(p),
        f"description: {p['description']}",
        "reviews: " + " | ".join(p["review_quotes"]),
    ])
    return _fence(body)


def _compare_products(args: dict, ctx: Context) -> str:
    ids = args.get("product_ids") or []
    if isinstance(ids, str):
        ids = [i.strip() for i in ids.split(",") if i.strip()]
    if not 2 <= len(ids) <= 3:
        return "ERROR: compare_products takes two or three product ids"
    rows = []
    for pid in ids:
        p = ctx.market.get_product(pid)
        rows.append(f"{p['name']}: {config.money(p['price'])}, {p['rating']}/5 ({p['reviews']} reviews), "
                    f"stock {p['stock']}, ships {p['ship_days']}d, {_variants(p)}\n"
                    f"   {p['description']}")
    return _fence("\n".join(rows))


# ---- cart ------------------------------------------------------------------

def _add_to_cart(args: dict, ctx: Context) -> str:
    product = ctx.market.get_product(args["product_id"])
    qty = int(args.get("quantity") or 1)
    if qty < 1:
        return "ERROR: quantity must be at least 1"
    if qty > product["stock"]:
        return f"ERROR: only {product['stock']} of {product['name']} in stock"
    options = args.get("options") or {}
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except json.JSONDecodeError:
            return "ERROR: options must be an object like {\"size\": \"42\"}"
    variant = ctx.market.check_variant(product, options)
    line = ctx.store.add_line(product, variant, qty)
    return (f"added {line['quantity']} x {product['name']} "
            f"{json.dumps(variant, ensure_ascii=False) if variant else ''} as {line['line_id']}. "
            f"Cart subtotal {config.money(ctx.store.cart_subtotal())}. Nothing is bought yet.")


def _remove_from_cart(args: dict, ctx: Context) -> str:
    line_id = str(args["line_id"])
    qty = args.get("quantity")
    if qty is None or int(qty) <= 0:
        removed = ctx.store.remove_line(line_id)
        if removed is None:
            return f"ERROR: no cart line {line_id!r}"
        return f"removed {removed['name']}. Cart subtotal {config.money(ctx.store.cart_subtotal())}."
    line = ctx.store.set_quantity(line_id, int(qty))
    if line is None:
        return f"ERROR: no cart line {line_id!r}"
    return f"{line['name']} is now quantity {line['quantity']}. Subtotal {config.money(ctx.store.cart_subtotal())}."


def _view_cart(args: dict, ctx: Context) -> str:
    if not ctx.store.cart:
        return "The cart is empty."
    subtotal = ctx.store.cart_subtotal()
    standard = ctx.market.shipping_cost(subtotal, "standard")
    lines = [f"{l['line_id']}  {l['quantity']} x {l['name']} "
             f"{json.dumps(l['variant'], ensure_ascii=False) if l['variant'] else ''}  "
             f"{config.money(l['unit_price'])} each" for l in ctx.store.cart]
    lines.append(f"subtotal {config.money(subtotal)}; standard shipping {config.money(standard)}; "
                 f"total {config.money(subtotal + standard)}")
    return "\n".join(lines)


def _place_order(args: dict, ctx: Context, receipt: Receipt) -> str:
    speed = (args.get("shipping_speed") or "standard").lower()
    subtotal = ctx.store.cart_subtotal()
    shipping = ctx.market.shipping_cost(subtotal, speed)
    eta = _eta(ctx, speed)
    order = ctx.store.add_order(ctx.store.cart, subtotal, shipping, receipt.total, speed, eta,
                                str(args.get("note") or ""))
    ctx.store.clear_cart()
    return (f"Order {order['id']} placed. Charged {config.money(order['total'])} to "
            f"{ctx.store.profile['payment']}. Arrives about {eta}. The cart is now empty.")


def _eta(ctx: Context, speed: str) -> str:
    days = max([ctx.market.get_product(l["product_id"])["ship_days"] for l in ctx.store.cart] or [2])
    return (date.today() + timedelta(days=max(1, days - 1) if speed == "express" else days)).isoformat()


# ---- booking ---------------------------------------------------------------

def _search_restaurants(args: dict, ctx: Context) -> str:
    hits = ctx.market.search_restaurants(
        args["city"], cuisine=args.get("cuisine"), party_size=int(args.get("party_size") or 2),
        when=args.get("date"), max_price_level=args.get("max_price_level"))
    if not hits:
        return "Nothing free with those filters. Try another date, a smaller party, or no cuisine filter."
    body = "\n".join(
        f"{r['id']}  {r['name']}  {r['cuisine']}, price {r['price_level']}/4, {r['rating']}/5, {r['address']}\n"
        f"   free: {', '.join(r['free_slots']) or 'no table that day'}"
        + (f"\n   deposit {config.money(r['deposit_per_person'])} per person" if r.get("deposit_per_person") else "")
        + f"\n   {r['note']}" for r in hits)
    return _fence(body)


def _book_restaurant(args: dict, ctx: Context, receipt: Receipt) -> str:
    r = ctx.market.get_restaurant(args["restaurant_id"])
    day = parse_date(args["date"])
    party = int(args["party_size"])
    slot = str(args["time"])
    booking = ctx.store.add_booking(
        "table", f"{r['name']}, table for {party}",
        f"{day.strftime('%A %d %b %Y')} at {slot}",
        {"restaurant_id": r["id"], "date": day.isoformat(), "time": slot, "party_size": party,
         "address": r["address"], "note": str(args.get("note") or "")},
        receipt.total,
        "deposit refunded if cancelled before the day" if receipt.total else "free to cancel")
    charged = f" Deposit of {config.money(receipt.total)} charged." if receipt.total else " No deposit."
    return (f"Booked {booking['id']}: {r['name']}, {party} people, {day.isoformat()} at {slot}."
            f"{charged} Address: {r['address']}.")


def _search_hotels(args: dict, ctx: Context) -> str:
    quotes = ctx.market.search_hotels(
        args["city"], args["check_in"], args["check_out"], guests=int(args.get("guests") or 1),
        max_nightly=args.get("max_nightly"))
    if not quotes:
        return "No room free for every night of that stay. Try other dates or a higher max_nightly."
    body = "\n".join(
        f"{q['hotel_id']}  {q['name']} {q['stars']}*, {q['rating']}/5, {q['address']}\n"
        f"   {q['nights']} night(s) in a {q['room_type']}: {config.money(q['avg_nightly'])}/night, "
        f"total {config.money(q['total'])}; breakfast {config.money(q['breakfast_per_person'])} pp\n"
        f"   free cancellation until {q['free_cancel_until']}. {q['note']}" for q in quotes)
    return _fence(body)


def _book_hotel(args: dict, ctx: Context, receipt: Receipt) -> str:
    q = ctx.market.quote_hotel(args["hotel_id"], args["check_in"], args["check_out"],
                               guests=int(args.get("guests") or 1),
                               room_type=(args.get("room_type") or "double").lower())
    booking = ctx.store.add_booking(
        "hotel", f"{q['name']}, {q['room_type']} room",
        f"{q['check_in']} to {q['check_out']} ({q['nights']} night(s))",
        {"hotel_id": q["hotel_id"], "check_in": q["check_in"], "check_out": q["check_out"],
         "guests": q["guests"], "room_type": q["room_type"], "address": q["address"],
         "free_cancel_until": q["free_cancel_until"]},
        receipt.total, f"free cancellation until {q['free_cancel_until']}")
    return (f"Booked {booking['id']}: {q['name']}, {q['room_type']} for {q['guests']}, "
            f"{q['check_in']} to {q['check_out']}. Charged {config.money(receipt.total)}. "
            f"Free cancellation until {q['free_cancel_until']}.")


def _search_flights(args: dict, ctx: Context) -> str:
    quotes = ctx.market.search_flights(
        args["origin"], args["destination"], args["date"],
        passengers=int(args.get("passengers") or 1), cabin=(args.get("cabin") or "economy").lower(),
        max_price=args.get("max_price"), nonstop_only=bool(args.get("nonstop_only")))
    if not quotes:
        return ("Nothing flying that fits. Try another date (fares drop midweek and more than "
                "two weeks out), another airport, or allow a stop.")
    body = "\n".join(
        f"{q['flight_id']}  {q['airline']} {q['number']}  {q['origin']}->{q['destination']}  "
        f"{q['weekday']} {q['date']} {q['dep']}-{q['arr']}  "
        f"{'nonstop' if not q['stops'] else str(q['stops']) + ' stop'}, {q['duration_min']} min\n"
        f"   {config.money(q['fare_per_passenger'])} per person in {q['cabin']}, "
        f"total {config.money(q['total'])} for {q['passengers']}; {q['seats_left']} seats left; {q['bags']}"
        for q in quotes)
    return _fence(body)


def _book_flight(args: dict, ctx: Context, receipt: Receipt) -> str:
    q = ctx.market.quote_flight(args["flight_id"], args["date"],
                                passengers=int(args.get("passengers") or 1),
                                cabin=(args.get("cabin") or "economy").lower())
    booking = ctx.store.add_booking(
        "flight", f"{q['airline']} {q['number']} {q['origin']}->{q['destination']}",
        f"{q['weekday']} {q['date']}, {q['dep']}-{q['arr']}",
        {"flight_id": q["flight_id"], "date": q["date"], "cabin": q["cabin"],
         "passengers": q["passengers"], "bags": q["bags"]},
        receipt.total, "airline tickets are not refundable")
    return (f"Booked {booking['id']}: {q['airline']} {q['number']}, {q['origin']} to "
            f"{q['destination']}, {q['date']} {q['dep']}. Charged {config.money(receipt.total)} "
            f"for {q['passengers']} in {q['cabin']}. Not refundable.")


# ---- account ---------------------------------------------------------------

def _list_orders(args: dict, ctx: Context) -> str:
    if not ctx.store.orders:
        return "No orders yet."
    return "\n".join(
        f"{o['id']}  {o['placed']}  {config.money(o['total'])}  {o['status']}  arrives {o['eta']}  "
        + ", ".join(f"{l['quantity']}x {l['name']}" for l in o["lines"]) for o in ctx.store.orders)


def _list_bookings(args: dict, ctx: Context) -> str:
    if not ctx.store.bookings:
        return "No bookings yet."
    return "\n".join(
        f"{b['id']}  {b['kind']}  {b['title']}  {b['when']}  {b['status']}"
        + (f"  paid {config.money(b['charged'])}" if b["charged"] else "")
        + (f"  ({b['cancel_policy']})" if b["cancel_policy"] else "") for b in ctx.store.bookings)


def _cancel_order(args: dict, ctx: Context, receipt: Receipt) -> str:
    order = ctx.store.cancel_order(args["order_id"])
    return f"Cancelled {order['id']}. {config.money(receipt.refund)} goes back to the card."


def _cancel_booking(args: dict, ctx: Context, receipt: Receipt) -> str:
    booking = ctx.store.cancel_booking(args["booking_id"], receipt.refund)
    refund = (f"{config.money(receipt.refund)} refunded." if receipt.refund
              else "No refund: that one was past its free cancellation.")
    return f"Cancelled {booking['id']} ({booking['title']}). {refund}"


_HANDLERS = {
    "today": _today, "get_profile": _get_profile, "set_preference": _set_preference,
    "search_products": _search_products, "get_product": _get_product,
    "compare_products": _compare_products, "add_to_cart": _add_to_cart,
    "remove_from_cart": _remove_from_cart, "view_cart": _view_cart, "place_order": _place_order,
    "search_restaurants": _search_restaurants, "book_restaurant": _book_restaurant,
    "search_hotels": _search_hotels, "book_hotel": _book_hotel,
    "search_flights": _search_flights, "book_flight": _book_flight,
    "list_orders": _list_orders, "list_bookings": _list_bookings,
    "cancel_order": _cancel_order, "cancel_booking": _cancel_booking,
}
