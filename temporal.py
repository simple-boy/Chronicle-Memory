"""Conservative calendar parsing against source timestamps, never wall clock.

Event strings preserve year/month/day precision. For compatibility, a calendar
week is represented by its Monday; ``expression`` preserves that coarser source
claim. Query windows always represent the complete period as [start, end).
Bare weekdays and numeric month/day dates are ambiguous and remain unresolved.
All source timestamps are Unix milliseconds and use the UTC calendar.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


_MONTHS = {}
for _number, _name in enumerate(calendar.month_name[1:], 1):
    _MONTHS[_name.casefold()] = _number
    _MONTHS[_name[:3].casefold()] = _number
_MONTHS["sept"] = 9
_MONTH_PATTERN = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_YEAR = r"(?:19|20)\d{2}"
_ISO = re.compile(
    rf"(?<![\d/-])(?P<year>{_YEAR})[-/](?P<month>\d{{1,2}})"
    r"(?:[-/](?P<day>\d{1,2}))?(?![\d/-])"
)
_CN = re.compile(
    rf"(?P<year>{_YEAR})年(?P<month>\d{{1,2}})月(?:(?P<day>\d{{1,2}})[日号])?"
)
_MONTH_DAY = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:\s*,?\s*(?P<year>{_YEAR}))?\b", re.I,
)
_DAY_MONTH = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    rf"(?P<month>{_MONTH_PATTERN})(?:\s*,?\s*(?P<year>{_YEAR}))?\b", re.I,
)
_MONTH_YEAR = re.compile(rf"\b(?P<month>{_MONTH_PATTERN})\s+(?P<year>{_YEAR})\b", re.I)
_YEAR_ONLY = re.compile(rf"(?<!\d){_YEAR}(?!\d)")
_AMBIGUOUS_NUMERIC = re.compile(rf"(?<![\d/-])\d{{1,2}}[/-]\d{{1,2}}[/-]{_YEAR}(?![\d/-])")
_NUMERIC_DATE_SHAPE = re.compile(rf"(?<![\d/-]){_YEAR}(?:[-/]\d*){{1,3}}(?![\d/-])")
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3,
                 "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10}
_COUNT = r"(?:\d{1,4}|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"
_RELATIVE = re.compile(
    rf"\b(?:yesterday|today|tomorrow|(?:last|this|next)\s+(?:day|week|month|year)"
    rf"|{_COUNT}\s+(?:days?|weeks?|months?|years?)\s+(?:ago|from\s+now))\b"
    r"|(?:昨天|昨日|今天|今日|明天|明日|上周|上星期|本周|这周|下周|下星期"
    r"|上个月|上月|这个月|本月|下个月|下月|去年|今年|明年|\d{1,4}(?:天|周|个?月|年)[前后])", re.I,
)
_WEEKDAYS = {name.casefold(): i for i, name in enumerate(calendar.day_name)}
_WEEKDAY_PATTERN = "(?:" + "|".join(_WEEKDAYS) + ")"
_WEEKDAY = re.compile(rf"\b(?:(?P<direction>last|next|this)\s+)?(?P<day>{_WEEKDAY_PATTERN})\b", re.I)
_COMPARATIVE = re.compile(r"\b(?:before|after|earlier\s+than|later\s+than|prior\s+to)\b|之前|之后|以前|以后|早于|晚于", re.I)


@dataclass(frozen=True)
class _Period:
    start: date
    end: date
    precision: str
    expression: str

    def event(self) -> tuple[str, int, str]:
        if self.precision == "year":
            return str(self.start.year), self.start.year, self.expression
        if self.precision == "month":
            return self.start.strftime("%Y-%m"), self.start.year * 100 + self.start.month, self.expression
        key = self.start.year * 10000 + self.start.month * 100 + self.start.day
        return self.start.isoformat(), key, self.expression

    def window(self) -> tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()


def _anchor(timestamp_ms: int | None) -> date | None:
    if timestamp_ms is None or isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        return None


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _period(year: int, month: int | None, day: int | None, expression: str) -> _Period:
    if day is not None:
        start = date(year, month, day)
        return _Period(start, start + timedelta(days=1), "day", expression)
    if month is not None:
        start = date(year, month, 1)
        return _Period(start, _shift_month(start, 1), "month", expression)
    return _Period(date(year, 1, 1), date(year + 1, 1, 1), "year", expression)


def _absolute_matches(text: str, anchor: date | None) -> list[tuple[int, int, _Period | None, str]]:
    """Match specific dates first so an invalid full date cannot become a year."""
    matches = [(match.start(), match.end(), None, match.group(0))
               for match in _AMBIGUOUS_NUMERIC.finditer(text)]
    matches.extend((match.start(), match.end(), None, match.group(0))
                   for match in _NUMERIC_DATE_SHAPE.finditer(text)
                   if _ISO.fullmatch(match.group(0)) is None)
    occupied = [(item[0], item[1]) for item in matches]
    for regex in (_ISO, _CN, _MONTH_DAY, _DAY_MONTH, _MONTH_YEAR, _YEAR_ONLY):
        for match in regex.finditer(text):
            start, end = match.span()
            if any(start < right and end > left for left, right in occupied):
                continue
            occupied.append((start, end))
            value = None
            expression = match.group(0)
            try:
                if regex is _YEAR_ONLY:
                    value = _period(int(expression), None, None, expression)
                else:
                    groups = match.groupdict()
                    year_text = groups.get("year")
                    year = int(year_text) if year_text else anchor.year if anchor else None
                    month_text = groups["month"].casefold().rstrip(".")
                    month = int(month_text) if month_text.isdigit() else _MONTHS[month_text]
                    day = int(groups["day"]) if groups.get("day") else None
                    if year is not None:
                        value = _period(year, month, day, expression)
            except (ValueError, OverflowError):
                pass
            matches.append((start, end, value, expression))
    return sorted(matches, key=lambda item: item[0])


def _relative_period(expression: str, anchor: date | None) -> _Period | None:
    if anchor is None:
        return None
    value = " ".join(expression.casefold().split())
    cn = {"昨天": "yesterday", "昨日": "yesterday", "今天": "today", "今日": "today",
          "明天": "tomorrow", "明日": "tomorrow", "上周": "last week", "上星期": "last week",
          "本周": "this week", "这周": "this week", "下周": "next week", "下星期": "next week",
          "上个月": "last month", "上月": "last month", "这个月": "this month", "本月": "this month",
          "下个月": "next month", "下月": "next month", "去年": "last year", "今年": "this year", "明年": "next year"}
    value = cn.get(value, value)
    daily = {"yesterday": -1, "today": 0, "tomorrow": 1}
    try:
        if value in daily:
            start = anchor + timedelta(days=daily[value])
            return _period(start.year, start.month, start.day, expression)
        named = re.fullmatch(r"(last|this|next) (day|week|month|year)", value)
        if named:
            direction = {"last": -1, "this": 0, "next": 1}[named[1]]
            unit = named[2]
            if unit == "week":
                start = anchor - timedelta(days=anchor.weekday()) + timedelta(weeks=direction)
                return _Period(start, start + timedelta(days=7), "week", expression)
            if unit == "month":
                start = _shift_month(anchor.replace(day=1), direction)
                return _period(start.year, start.month, None, expression)
            if unit == "year":
                return _period(anchor.year + direction, None, None, expression)
            start = anchor + timedelta(days=direction)
            return _period(start.year, start.month, start.day, expression)
        counted = re.fullmatch(rf"(?P<n>{_COUNT}) (?P<unit>days?|weeks?|months?|years?) (?P<direction>ago|from now)", value)
        chinese = re.fullmatch(r"(?P<n>\d{1,4})(?P<unit>天|周|个?月|年)(?P<direction>[前后])", value)
        if counted or chinese:
            match = counted or chinese
            number_text = match["n"]
            number = int(number_text) if number_text.isdigit() else _NUMBER_WORDS[number_text]
            direction = -1 if match["direction"] in ("ago", "前") else 1
            unit = match["unit"]
            if unit in ("天", "day", "days", "周", "week", "weeks"):
                weeks = unit in ("周", "week", "weeks")
                start = anchor + timedelta(days=direction * number * (7 if weeks else 1))
            else:
                years = unit in ("年", "year", "years")
                start = _shift_month(anchor, direction * number * (12 if years else 1))
            return _period(start.year, start.month, start.day, expression)
    except (ValueError, OverflowError):
        return None
    return None


def _weekday_period(match: re.Match, anchor: date | None) -> _Period | None:
    if anchor is None or not match["direction"]:
        return None
    target = _WEEKDAYS[match["day"].casefold()]
    direction = match["direction"].casefold()
    if direction == "last":
        offset = -((anchor.weekday() - target) % 7 or 7)
    elif direction == "next":
        offset = (target - anchor.weekday()) % 7 or 7
    else:
        offset = target - anchor.weekday()
    try:
        start = anchor + timedelta(days=offset)
        return _period(start.year, start.month, start.day, match.group(0))
    except (ValueError, OverflowError):
        return None


def _matches(text: str, anchor: date | None) -> list[tuple[int, int, _Period | None, str]]:
    matches = _absolute_matches(text, anchor)
    for regex in (_RELATIVE, _WEEKDAY):
        for match in regex.finditer(text):
            if any(match.start() < right and match.end() > left for left, right, *_ in matches):
                continue
            if regex is _WEEKDAY:
                # A weekday immediately attached to a full calendar date is
                # redundant evidence, not a separate unanchored relative date.
                # Validate it rather than silently ignoring a contradiction.
                attached = None
                for index, (left, right, absolute, expression) in enumerate(matches):
                    if absolute is None or absolute.precision != "day":
                        continue
                    gap = text[match.end():left] if match.end() <= left else text[right:match.start()] if right <= match.start() else None
                    if gap is not None and re.fullmatch(r"[\s,，()\-]*", gap):
                        attached = index
                        break
                if attached is not None:
                    left, right, absolute, _ = matches[attached]
                    left, right = min(left, match.start()), max(right, match.end())
                    expression = text[left:right]
                    consistent = absolute.start.weekday() == _WEEKDAYS[match["day"].casefold()]
                    value = _Period(absolute.start, absolute.end, absolute.precision, expression) if consistent else None
                    matches[attached] = (left, right, value, expression)
                    continue
            period = _relative_period(match.group(0), anchor) if regex is _RELATIVE else _weekday_period(match, anchor)
            matches.append((match.start(), match.end(), period, match.group(0)))
    return sorted(matches, key=lambda item: item[0])


def parse_event_time(content: str, timestamp_ms: int | None = None) -> tuple[str | None, int | None, str | None]:
    """Resolve the first source time expression, with no date invented from now.

    Absolute years/months retain their precision and legacy integer key format.
    A month/day without a year uses an explicit source anchor's year only.
    Unresolved relative/weekday/month-day expressions retain their text.
    """
    matches = _matches(content, _anchor(timestamp_ms))
    if not matches:
        return None, None, None
    _, _, period, expression = matches[0]
    if period:
        return period.event()
    return None, None, expression


def query_time_window(query: str, anchor_timestamp_ms: int | None = None) -> tuple[str, str] | None:
    """Return a UTC calendar [start, end) window, never a before/after filter.

    Day/month/year end dates are exclusive. Explicit ``from X to Y`` or
    ``between X and Y`` include all of Y's stated period. Ambiguous multiple
    dates, reversed ranges, invalid dates, and unanchored relatives yield None.
    The caller must document where any supplied query anchor came from.
    """
    if _COMPARATIVE.search(query):
        return None
    matches = _matches(query, _anchor(anchor_timestamp_ms))
    if not matches or any(item[2] is None for item in matches):
        return None
    if len(matches) == 1:
        return matches[0][2].window()
    if len(matches) != 2:
        return None
    first, second = matches
    prefix = query[:first[0]]
    connector = query[first[1]:second[0]]
    is_range = (
        re.search(r"\bfrom\s*$|从\s*$", prefix, re.I) and re.fullmatch(r"\s*(?:to|through|至|到)\s*", connector, re.I)
        or re.search(r"\bbetween\s*$", prefix, re.I) and re.fullmatch(r"\s*and\s*", connector, re.I)
    )
    if not is_range or second[2].start < first[2].start:
        return None
    return first[2].start.isoformat(), second[2].end.isoformat()


def event_time_windows(content: str, timestamp_ms: int | None = None) -> list[tuple[str, str]]:
    """Return source calendar periods, including interior days of explicit ranges.

    Independent expressions retain separate windows; they do not imply a
    continuous stay. ``from X to/through Y`` and ``between X and Y`` include
    Y's full stated period. A year stated on one English month/day endpoint
    also qualifies the other endpoint. Reversed, invalid, unanchored and
    incomplete ranges yield no window rather than two misleading endpoints.
    This is lexical temporal metadata, not event entailment or a timezone model.
    """
    matches = _matches(content, _anchor(timestamp_ms))
    windows = []
    index = 0
    while index < len(matches):
        first = matches[index]
        prefix = content[:first[0]]
        from_start = bool(re.search(r"\bfrom\s*$|从\s*$", prefix, re.I))
        between_start = bool(re.search(r"\bbetween\s*$", prefix, re.I))
        connector_re = r"\s*(?:to|through|至|到)\s*" if from_start else r"\s*and\s*"
        if (from_start or between_start) and index + 1 < len(matches):
            second = matches[index + 1]
            connector = content[first[1]:second[0]]
            if re.fullmatch(connector_re, connector, re.I):
                resolved = [first[2], second[2]]
                for endpoint, peer in ((0, 1), (1, 0)):
                    expression = (first, second)[endpoint][3]
                    peer_expression = (first, second)[peer][3]
                    short_date = _MONTH_DAY.fullmatch(expression) or _DAY_MONTH.fullmatch(expression)
                    shared_year = _YEAR_ONLY.search(peer_expression)
                    if short_date and not short_date.groupdict().get("year") and shared_year:
                        # Explicit endpoint year outranks the report's year.
                        parsed = _absolute_matches(expression, date(int(shared_year.group(0)), 1, 1))
                        resolved[endpoint] = parsed[0][2] if len(parsed) == 1 else None
                left, right = resolved
                if left and right and right.start >= left.start:
                    windows.append((left.start.isoformat(), right.end.isoformat()))
                index += 2
                continue
        if from_start or between_start:
            tail = content[first[1]:]
            partial_connector = r"\s*(?:to|through|至|到)\b" if from_start else r"\s*and\b"
            if re.match(partial_connector, tail, re.I):
                index += 1
                continue
        if first[2] is not None:
            windows.append(first[2].window())
        index += 1
    return list(dict.fromkeys(windows))
