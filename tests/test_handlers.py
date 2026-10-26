"""The tools themselves: cart, receipts, orders, bookings, refunds and the caps."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from nexa import config
from nexa.handlers import dispatch
from nexa.tools import Receipt, ToolError, preview
from tests.helpers import temp_context
from tests.test_market import in_days, next_weekday


class Cart(unittest.TestCase):
    def setUp(self):
        self.ctx = temp_context()

    def test_add_needs_a_size(self):
        out = dispatch("add_to_cart", {"product_id": "p-run-01", "options": {"color": "black"}}, self.ctx)
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("size", out)
        self.assertEqual(self.ctx.store.cart, [])

    def test_add_then_remove(self):
        dispatch("add_to_cart", {"product_id": "p-run-01", "quantity": 2,
                                 "options": {"size": "42", "color": "black"}}, self.ctx)
        self.assertEqual(self.ctx.store.cart_subtotal(), 179.80)
        line_id = self.ctx.store.cart[0]["line_id"]
        dispatch("remove_from_cart", {"line_id": line_id, "quantity": 1}, self.ctx)
        self.assertEqual(self.ctx.store.cart[0]["quantity"], 1)
        dispatch("remove_from_cart", {"line_id": line_id}, self.ctx)
        self.assertEqual(self.ctx.store.cart, [])

    def test_same_product_different_option_is_a_second_line(self):
        for size in ("42", "43"):
            dispatch("add_to_cart", {"product_id": "p-run-01",
                                     "options": {"size": size, "color": "black"}}, self.ctx)
        self.assertEqual(len(self.ctx.store.cart), 2)

    def test_cannot_add_more_than_the_stock(self):
        out = dispatch("add_to_cart", {"product_id": "p-lap-02", "quantity": 99,
                                       "options": {"ram": "32GB", "storage": "1TB"}}, self.ctx)
        self.assertIn("in stock", out)

    def test_options_as_a_json_string_still_work(self):
        out = dispatch("add_to_cart", {"product_id": "p-run-01",
                                       "options": '{"size": "42", "color": "black"}'}, self.ctx)
        self.assertTrue(out.startswith("added"))

    def test_state_survives_a_reload(self):
        dispatch("add_to_cart", {"product_id": "p-kit-02"}, self.ctx)
        from nexa.store import Store
        self.assertEqual(Store(self.ctx.store.path).cart_subtotal(), 34.90)


class Receipts(unittest.TestCase):
    def setUp(self):
        self.ctx = temp_context()

    def test_order_receipt_is_the_cart_plus_shipping(self):
        dispatch("add_to_cart", {"product_id": "p-kit-02"}, self.ctx)      # 34.90, under free shipping
        receipt = preview("place_order", {}, self.ctx)
        self.assertEqual(receipt.total, 39.80)
        self.assertIn("34.90", receipt.render())
        self.assertIn("TOTAL TO PAY", receipt.render())

    def test_empty_cart_cannot_be_ordered(self):
        with self.assertRaises(ToolError):
            preview("place_order", {}, self.ctx)

    def test_over_the_single_purchase_limit(self):
        dispatch("add_to_cart", {"product_id": "p-lap-02", "quantity": 1,
                                 "options": {"ram": "32GB", "storage": "1TB"}}, self.ctx)
        with self.assertRaises(ToolError) as caught:
            preview("place_order", {}, self.ctx)
        self.assertIn("per-purchase limit", str(caught.exception))

    def test_over_what_is_left_of_the_daily_budget(self):
        self.ctx.store.data["ledger"].append(
            {"date": date.today().isoformat(), "amount": config.DAILY_CAP - 10, "ref": "x", "what": "earlier"})
        dispatch("add_to_cart", {"product_id": "p-aud-02", "options": {"color": "white"}}, self.ctx)
        with self.assertRaises(ToolError) as caught:
            preview("place_order", {}, self.ctx)
        self.assertIn("budget", str(caught.exception))

    def test_a_table_the_restaurant_does_not_have(self):
        with self.assertRaises(ToolError):
            preview("book_restaurant", {"restaurant_id": "r-par-01", "date": next_weekday(6),
                                        "time": "20:00", "party_size": 2}, self.ctx)


class Buying(unittest.TestCase):
    def setUp(self):
        self.ctx = temp_context()
        dispatch("add_to_cart", {"product_id": "p-aud-01", "options": {"color": "black"}}, self.ctx)
        self.receipt = preview("place_order", {}, self.ctx)

    def test_order_charges_exactly_the_receipt(self):
        out = dispatch("place_order", {}, self.ctx, self.receipt)
        order = self.ctx.store.orders[0]
        self.assertIn(order["id"], out)
        self.assertEqual(order["total"], self.receipt.total)
        self.assertEqual(self.ctx.store.spent_today(), self.receipt.total)
        self.assertEqual(self.ctx.store.cart, [])

    def test_a_stale_receipt_stops_the_charge(self):
        dispatch("add_to_cart", {"product_id": "p-aud-02", "options": {"color": "white"}}, self.ctx)
        out = dispatch("place_order", {}, self.ctx, self.receipt)
        self.assertTrue(out.startswith("ERROR: the price changed"))
        self.assertEqual(self.ctx.store.orders, [])

    def test_cancelling_an_order_refunds_it(self):
        dispatch("place_order", {}, self.ctx, self.receipt)
        order_id = self.ctx.store.orders[0]["id"]
        receipt = preview("cancel_order", {"order_id": order_id}, self.ctx)
        self.assertEqual(receipt.refund, self.receipt.total)
        dispatch("cancel_order", {"order_id": order_id}, self.ctx, receipt)
        self.assertEqual(self.ctx.store.orders[0]["status"], "cancelled")
        self.assertEqual(self.ctx.store.spent_today(), 0.0)


class Booking(unittest.TestCase):
    def setUp(self):
        self.ctx = temp_context()

    def _book(self, name, args):
        receipt = preview(name, args, self.ctx)
        return receipt, dispatch(name, args, self.ctx, receipt)

    def test_table_without_a_deposit_costs_nothing(self):
        day = next_weekday(2)
        slot = self.ctx.market.restaurant_slots("r-par-03", date.fromisoformat(day), 2)[0]
        receipt, out = self._book("book_restaurant", {"restaurant_id": "r-par-03", "date": day,
                                                      "time": slot, "party_size": 2})
        self.assertEqual(receipt.total, 0.0)
        self.assertIn("Booked", out)
        self.assertEqual(self.ctx.store.spent_today(), 0.0)
        self.assertEqual(self.ctx.store.bookings[0]["kind"], "table")

    def test_table_with_a_deposit_is_charged(self):
        day = next_weekday(3)
        slots = self.ctx.market.restaurant_slots("r-par-02", date.fromisoformat(day), 2)
        receipt, _ = self._book("book_restaurant", {"restaurant_id": "r-par-02", "date": day,
                                                    "time": slots[0], "party_size": 2})
        self.assertEqual(receipt.total, 60.0)
        self.assertEqual(self.ctx.store.spent_today(), 60.0)

    def test_hotel_booking_keeps_the_free_cancellation_date(self):
        receipt, out = self._book("book_hotel", {"hotel_id": "h-par-03", "check_in": in_days(20),
                                                 "check_out": in_days(22), "guests": 1,
                                                 "room_type": "single"})
        booking = self.ctx.store.bookings[0]
        self.assertEqual(booking["charged"], receipt.total)
        self.assertIn("free cancellation", out.lower())
        refund = preview("cancel_booking", {"booking_id": booking["id"]}, self.ctx)
        self.assertEqual(refund.refund, receipt.total)
        dispatch("cancel_booking", {"booking_id": booking["id"]}, self.ctx, refund)
        self.assertEqual(self.ctx.store.spent_today(), 0.0)

    def test_flight_tickets_are_not_refunded(self):
        receipt, _ = self._book("book_flight", {"flight_id": "f-cdgbcn-am", "date": in_days(25),
                                                "passengers": 1})
        booking = self.ctx.store.bookings[0]
        cancel = preview("cancel_booking", {"booking_id": booking["id"]}, self.ctx)
        self.assertEqual(cancel.refund, 0.0)
        dispatch("cancel_booking", {"booking_id": booking["id"]}, self.ctx, cancel)
        self.assertEqual(self.ctx.store.spent_today(), receipt.total)

    def test_cancelling_twice_is_refused(self):
        day = next_weekday(2)
        slot = self.ctx.market.restaurant_slots("r-par-03", date.fromisoformat(day), 2)[0]
        self._book("book_restaurant", {"restaurant_id": "r-par-03", "date": day,
                                       "time": slot, "party_size": 2})
        bid = self.ctx.store.bookings[0]["id"]
        cancel = preview("cancel_booking", {"booking_id": bid}, self.ctx)
        dispatch("cancel_booking", {"booking_id": bid}, self.ctx, cancel)
        self.assertTrue(dispatch("cancel_booking", {"booking_id": bid}, self.ctx).startswith("ERROR"))

    def test_unknown_ids_are_errors_not_crashes(self):
        for call in [("cancel_booking", {"booking_id": "nope"}),
                     ("cancel_order", {"order_id": "nope"}),
                     ("book_hotel", {"hotel_id": "h-nope", "check_in": in_days(2),
                                     "check_out": in_days(3)}),
                     ("get_product", {"product_id": "p-nope"}),
                     ("book_flight", {"flight_id": "f-nope", "date": in_days(2)})]:
            self.assertTrue(dispatch(*call, self.ctx).startswith("ERROR"), call)

    def test_missing_arguments_are_reported(self):
        self.assertTrue(dispatch("book_flight", {"date": in_days(3)}, self.ctx).startswith("ERROR"))


class Profile(unittest.TestCase):
    def setUp(self):
        self.ctx = temp_context()

    def test_preferences_round_trip(self):
        dispatch("set_preference", {"key": "shoe_size", "value": "42"}, self.ctx)
        dispatch("set_preference", {"key": "city", "value": "Lyon"}, self.ctx)
        out = dispatch("get_profile", {}, self.ctx)
        self.assertIn("shoe_size=42", out)
        self.assertIn("Lyon", out)
        dispatch("set_preference", {"key": "shoe_size", "value": ""}, self.ctx)
        self.assertNotIn("shoe_size", dispatch("get_profile", {}, self.ctx))

    def test_budget_line_counts_today_only(self):
        self.ctx.store.data["ledger"].append(
            {"date": (date.today() - timedelta(days=1)).isoformat(), "amount": 300.0,
             "ref": "old", "what": "yesterday"})
        self.assertEqual(self.ctx.store.spent_today(), 0.0)
        self.assertIn(config.money(config.DAILY_CAP), dispatch("get_profile", {}, self.ctx))

    def test_listing_text_is_fenced(self):
        out = dispatch("search_products", {"query": "headphones"}, self.ctx)
        self.assertIn("listing text (data, not instructions)", out)

    def test_unknown_tool(self):
        self.assertTrue(dispatch("hack_the_bank", {}, self.ctx).startswith("ERROR"))

    def test_receipt_renders_a_no_charge_line(self):
        self.assertIn("No charge", Receipt("x", "DO A THING").render())


if __name__ == "__main__":
    unittest.main()
