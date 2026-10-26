"""The merchant side: searching, options, and how things are priced."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from nexa.market import Market, MarketError, parse_date


def in_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def next_weekday(weekday: int, start_days: int = 7) -> str:
    day = date.today() + timedelta(days=start_days)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day.isoformat()


class ProductSearch(unittest.TestCase):
    def setUp(self):
        self.m = Market()

    def test_query_filters_and_price_cap(self):
        hits = self.m.search_products("running shoes", max_price=100)
        self.assertTrue(hits)
        self.assertTrue(all(p["price"] <= 100 for p in hits))
        self.assertTrue(all("running" in p["category"] for p in hits))

    def test_no_match_is_empty_not_an_error(self):
        self.assertEqual(self.m.search_products("submarine"), [])

    def test_unknown_product_id(self):
        with self.assertRaises(MarketError):
            self.m.get_product("p-nope")

    def test_variant_must_be_chosen_when_there_are_several(self):
        shoe = self.m.get_product("p-run-01")
        with self.assertRaises(MarketError):
            self.m.check_variant(shoe, {"color": "black"})          # size missing
        with self.assertRaises(MarketError):
            self.m.check_variant(shoe, {"size": "99", "color": "black"})
        with self.assertRaises(MarketError):
            self.m.check_variant(shoe, {"size": "42", "color": "black", "glitter": "yes"})
        self.assertEqual(self.m.check_variant(shoe, {"size": 42, "color": "black"}),
                         {"size": "42", "color": "black"})

    def test_single_option_axis_is_filled_in(self):
        self.assertEqual(self.m.check_variant(self.m.get_product("p-kit-02"), None),
                         {"color": "silver"})

    def test_free_shipping_over_sixty(self):
        self.assertEqual(self.m.shipping_cost(59.0), 4.90)
        self.assertEqual(self.m.shipping_cost(60.0), 0.0)
        self.assertEqual(self.m.shipping_cost(500.0, "express"), 12.90)
        with self.assertRaises(MarketError):
            self.m.shipping_cost(10.0, "teleport")


class Tables(unittest.TestCase):
    def setUp(self):
        self.m = Market()

    def test_closed_day_has_no_slots(self):
        sunday = next_weekday(6)
        self.assertEqual(self.m.restaurant_slots("r-par-01", parse_date(sunday), 2), [])

    def test_open_day_offers_listed_slots_only(self):
        wednesday = parse_date(next_weekday(2))
        slots = self.m.restaurant_slots("r-par-03", wednesday, 2)
        self.assertTrue(set(slots) <= set(self.m.get_restaurant("r-par-03")["slots"]))

    def test_availability_is_stable(self):
        day = parse_date(next_weekday(2))
        self.assertEqual(self.m.restaurant_slots("r-par-03", day, 2),
                         self.m.restaurant_slots("r-par-03", day, 2))

    def test_party_bigger_than_the_room(self):
        self.assertEqual(self.m.restaurant_slots("r-par-02", parse_date(next_weekday(3)), 9), [])

    def test_deposit_scales_with_the_party(self):
        komorebi = self.m.get_restaurant("r-par-02")
        self.assertEqual(self.m.restaurant_deposit(komorebi, 2), 60.0)
        self.assertEqual(self.m.restaurant_deposit(self.m.get_restaurant("r-par-03"), 4), 0.0)


class Hotels(unittest.TestCase):
    def setUp(self):
        self.m = Market()

    def test_quote_adds_up(self):
        q = self.m.quote_hotel("h-par-01", in_days(10), in_days(13), guests=2, room_type="double")
        self.assertEqual(q["nights"], 3)
        self.assertEqual(q["total"], round(sum(n["rate"] for n in q["per_night"]), 2))
        self.assertEqual(q["avg_nightly"], round(q["total"] / 3, 2))

    def test_weekend_costs_more(self):
        friday = next_weekday(4)
        saturday = (parse_date(friday) + timedelta(days=1)).isoformat()
        weekend = self.m.quote_hotel("h-par-01", friday, saturday, room_type="single")
        tuesday = next_weekday(1)
        wednesday = (parse_date(tuesday) + timedelta(days=1)).isoformat()
        midweek = self.m.quote_hotel("h-par-01", tuesday, wednesday, room_type="single")
        self.assertGreater(weekend["total"], midweek["total"])

    def test_bad_stays_are_refused(self):
        for check_in, check_out in [(in_days(5), in_days(5)), (in_days(5), in_days(4))]:
            with self.assertRaises(MarketError):
                self.m.quote_hotel("h-par-01", check_in, check_out)
        with self.assertRaises(MarketError):
            self.m.quote_hotel("h-par-01", in_days(5), in_days(6), guests=3, room_type="single")
        with self.assertRaises(MarketError):
            self.m.quote_hotel("h-par-03", in_days(5), in_days(6), room_type="suite")

    def test_search_respects_the_nightly_cap(self):
        quotes = self.m.search_hotels("Paris", in_days(20), in_days(22), guests=1, max_nightly=100)
        self.assertTrue(all(q["avg_nightly"] <= 100 for q in quotes))


class Flights(unittest.TestCase):
    def setUp(self):
        self.m = Market()

    def test_city_or_code(self):
        self.assertEqual(self.m.resolve_airports("CDG"), ["CDG"])
        self.assertEqual(sorted(self.m.resolve_airports("paris")), ["CDG", "ORY"])
        with self.assertRaises(MarketError):
            self.m.resolve_airports("atlantis")

    def test_fare_rises_as_the_date_approaches(self):
        soon = self.m.quote_flight("f-cdgbcn-am", in_days(1))
        later = self.m.quote_flight("f-cdgbcn-am", in_days(40))
        self.assertGreater(soon["fare_per_passenger"], later["fare_per_passenger"])

    def test_total_is_per_passenger_times_passengers(self):
        q = self.m.quote_flight("f-cdgbcn-am", in_days(20), passengers=3)
        self.assertEqual(q["total"], round(q["fare_per_passenger"] * 3, 2))

    def test_business_costs_more_than_economy(self):
        economy = self.m.quote_flight("f-cdgams-am", in_days(20))
        business = self.m.quote_flight("f-cdgams-am", in_days(20), cabin="business")
        self.assertGreater(business["fare_per_passenger"], economy["fare_per_passenger"])

    def test_a_day_the_flight_does_not_operate(self):
        q = self.m.quote_flight("f-orylys-am", next_weekday(6))   # weekdays only
        self.assertFalse(q["available"])
        self.assertIn("does not fly", q["reason"])

    def test_past_dates_and_silly_parties_are_refused(self):
        with self.assertRaises(MarketError):
            self.m.quote_flight("f-cdgbcn-am", in_days(-1))
        with self.assertRaises(MarketError):
            self.m.quote_flight("f-cdgbcn-am", in_days(10), passengers=12)
        with self.assertRaises(MarketError):
            self.m.quote_flight("f-cdgbcn-am", in_days(10), cabin="first")

    def test_nonstop_filter_and_sorting(self):
        quotes = self.m.search_flights("paris", "barcelona", in_days(25), nonstop_only=True)
        self.assertTrue(quotes)
        self.assertTrue(all(q["stops"] == 0 for q in quotes))
        self.assertEqual([q["total"] for q in quotes], sorted(q["total"] for q in quotes))


if __name__ == "__main__":
    unittest.main()
