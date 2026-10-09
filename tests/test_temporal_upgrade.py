"""Calendar/date-parser regressions; deterministic synthetic cases, no model."""

import unittest
from datetime import datetime, timezone

from temporal import event_time_windows, parse_event_time, query_time_window


def ts(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


class EventTimeUpgradeTests(unittest.TestCase):
    def test_legacy_precision_is_preserved(self):
        for text, value, key in (("in 2025", "2025", 2025), ("in 2025-02", "2025-02", 202502),
                                 ("on 2025/2/3", "2025-02-03", 20250203),
                                 ("2025年2月3日", "2025-02-03", 20250203),
                                 ("February 2025", "2025-02", 202502)):
            with self.subTest(text=text):
                actual = parse_event_time(text)
                self.assertEqual(actual[:2], (value, key))
                self.assertTrue(actual[2])

    def test_english_full_dates_and_abbreviations(self):
        for text in ("May 3rd, 2025", "3 May 2025", "3rd of May 2025", "May 3 2025"):
            with self.subTest(text=text):
                self.assertEqual(parse_event_time(text)[:2], ("2025-05-03", 20250503))
        self.assertEqual(parse_event_time("Sept. 12th, 2024")[:2], ("2024-09-12", 20240912))
        self.assertEqual(parse_event_time("12 Sep 2024")[:2], ("2024-09-12", 20240912))
        self.assertEqual(parse_event_time("Feb. 2024")[:2], ("2024-02", 202402))

    def test_month_day_requires_explicit_source_anchor(self):
        self.assertEqual(parse_event_time("May 3rd")[:2], (None, None))
        self.assertEqual(parse_event_time("May 3rd", ts("2026-10-09"))[:2], ("2026-05-03", 20260503))

    def test_invalid_full_date_cannot_fall_back_to_year(self):
        for text in ("2025-02-29", "2025-13-03", "2025-04-31", "2025-03-003", "2025-02-", "February 30, 2025", "2025年13月3日"):
            with self.subTest(text=text):
                self.assertEqual(parse_event_time(text)[:2], (None, None))

    def test_relative_without_source_anchor_remains_unknown(self):
        for text in ("yesterday", "last week", "2 days ago", "next month", "last Friday"):
            with self.subTest(text=text):
                self.assertEqual(parse_event_time(text), (None, None, text))

    def test_relative_days_and_weeks_use_source_utc_date(self):
        anchor = ts("2025-03-03T00:30:00")
        for text, value in (("yesterday", "2025-03-02"), ("tomorrow", "2025-03-04"),
                            ("3 days ago", "2025-02-28"), ("two weeks ago", "2025-02-17"),
                            ("2天前", "2025-03-01"), ("3 weeks from now", "2025-03-24")):
            with self.subTest(text=text):
                self.assertEqual(parse_event_time(text, anchor)[0], value)

    def test_calendar_month_and_year_precision(self):
        anchor = ts("2025-01-31")
        self.assertEqual(parse_event_time("last month", anchor)[:2], ("2024-12", 202412))
        self.assertEqual(parse_event_time("next month", anchor)[:2], ("2025-02", 202502))
        self.assertEqual(parse_event_time("last year", anchor)[:2], ("2024", 2024))
        self.assertEqual(parse_event_time("明年", anchor)[:2], ("2026", 2026))
        self.assertEqual(parse_event_time("1 month ago", anchor)[0], "2024-12-31")
        self.assertEqual(parse_event_time("1 month from now", anchor)[0], "2025-02-28")

    def test_weekday_explicit_direction_and_ambiguity(self):
        anchor = ts("2025-03-03")  # Monday
        self.assertEqual(parse_event_time("last Monday", anchor)[0], "2025-02-24")
        self.assertEqual(parse_event_time("next Monday", anchor)[0], "2025-03-10")
        self.assertEqual(parse_event_time("this Friday", anchor)[0], "2025-03-07")
        self.assertEqual(parse_event_time("on Monday", anchor)[:2], (None, None))

    def test_attached_weekday_calendar_date_must_be_consistent(self):
        self.assertEqual(parse_event_time("Saturday, May 3rd, 2025")[0], "2025-05-03")
        self.assertEqual(parse_event_time("May 3, 2025 (Saturday)")[0], "2025-05-03")
        self.assertEqual(parse_event_time("Monday, May 3rd, 2025")[:2], (None, None))
        self.assertIsNone(query_time_window("What happened on Monday, May 3rd, 2025?"))

    def test_invalid_anchor_does_not_use_wall_clock(self):
        for anchor in (None, True, "2025-03-03", float("inf"), 10**100):
            with self.subTest(anchor=anchor):
                self.assertEqual(parse_event_time("yesterday", anchor)[:2], (None, None))
        self.assertEqual(parse_event_time("2025-03-03", None)[0], "2025-03-03")


class QueryWindowUpgradeTests(unittest.TestCase):
    def test_day_window_end_is_exclusive(self):
        self.assertEqual(query_time_window("What happened on May 3rd, 2025?"), ("2025-05-03", "2025-05-04"))
        self.assertEqual(query_time_window("What happened yesterday?", ts("2025-03-03")), ("2025-03-02", "2025-03-03"))

    def test_entire_calendar_week_not_only_monday(self):
        anchor = ts("2025-01-01")
        self.assertEqual(query_time_window("What did I do last week?", anchor), ("2024-12-23", "2024-12-30"))
        self.assertEqual(query_time_window("What happened this week?", anchor), ("2024-12-30", "2025-01-06"))
        self.assertEqual(query_time_window("What will happen next week?", anchor), ("2025-01-06", "2025-01-13"))

    def test_complete_month_and_year_window(self):
        anchor = ts("2024-03-20")
        self.assertEqual(query_time_window("What happened last month?", anchor), ("2024-02-01", "2024-03-01"))
        self.assertEqual(query_time_window("What happened in February 2024?"), ("2024-02-01", "2024-03-01"))
        self.assertEqual(query_time_window("What happened in 2025?"), ("2025-01-01", "2026-01-01"))
        self.assertEqual(query_time_window("What happened 去年?", anchor), ("2023-01-01", "2024-01-01"))

    def test_explicit_inclusive_calendar_ranges(self):
        self.assertEqual(query_time_window("What happened from 2025-03-03 to 2025-03-05?"), ("2025-03-03", "2025-03-06"))
        self.assertEqual(query_time_window("between May 3, 2025 and May 5, 2025"), ("2025-05-03", "2025-05-06"))
        self.assertEqual(query_time_window("from March 2025 through April 2025"), ("2025-03-01", "2025-05-01"))
        self.assertEqual(query_time_window("从2025年3月3日到2025年3月5日"), ("2025-03-03", "2025-03-06"))

    def test_before_after_is_not_an_equality_window(self):
        for text in ("before 2025-03-03", "after May 3, 2025", "earlier than 2025", "2025年3月3日之后", "last week after lunch"):
            with self.subTest(text=text):
                self.assertIsNone(query_time_window(text, ts("2025-03-03")))

    def test_ambiguous_and_reversed_ranges_remain_unknown(self):
        for text in ("2025-03-03 or 2025-03-05", "from 2025-03-05 to 2025-03-03", "from May 3 to May 5", "last week", "on Monday", "on 03/05/2025", "on 2025-02-29"):
            with self.subTest(text=text):
                self.assertIsNone(query_time_window(text))


class EventWindowUpgradeTests(unittest.TestCase):
    def test_stay_range_contains_interior_day_with_shared_explicit_year(self):
        text = "I visited the Project Atlas00 workshop in Oslo from June 10 to June 12, 2026."
        expected = [("2026-06-10", "2026-06-13")]
        self.assertEqual(event_time_windows(text), expected)
        self.assertEqual(event_time_windows(text, ts("2027-07-01")), expected)
        start, end = event_time_windows(text)[0]
        self.assertTrue(start <= "2026-06-11" < end)
        self.assertEqual(event_time_windows("I stayed from 2026-06-10 through 2026-06-12."), expected)

    def test_invalid_reversed_and_unanchored_ranges_do_not_become_endpoints(self):
        for text in ("from June 10 to June 12", "from June 12, 2026 to June 10, 2026",
                     "between February 29, 2025 and March 3, 2025", "from June 10, 2026 to an unknown date",
                     "from Monday, May 3, 2025 to May 5, 2025"):
            with self.subTest(text=text):
                self.assertEqual(event_time_windows(text), [])

    def test_source_period_precision_and_independent_dates(self):
        self.assertEqual(event_time_windows("The project ran in 2026."), [("2026-01-01", "2027-01-01")])
        self.assertEqual(event_time_windows("The project ran in February 2024."), [("2024-02-01", "2024-03-01")])
        self.assertEqual(event_time_windows("I travelled last week.", ts("2025-01-01")), [("2024-12-23", "2024-12-30")])
        self.assertEqual(event_time_windows("I visited on June 10, 2026. I visited again on June 12, 2026."),
                         [("2026-06-10", "2026-06-11"), ("2026-06-12", "2026-06-13")])


if __name__ == "__main__":
    unittest.main()
