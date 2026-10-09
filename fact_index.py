"""Small, conservative explicit-fact parser with exact source evidence.

This is a lexical index, not an entailment model. Unsupported assertions stay in
the original message; no facts are inferred from questions or hypothetical text.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


_ATTRS = {
    "phone": "phone", "phone number": "phone", "telephone": "phone",
    "telephone number": "phone", "mobile": "phone", "mobile number": "phone",
    "email": "email", "e-mail": "email", "email address": "email",
    "address": "address", "home address": "address", "name": "name",
    "employer": "employer", "company": "employer", "occupation": "occupation",
    "job": "occupation", "location": "location", "residence": "location",
    "database": "uses:database", "数据库": "uses:database",
    "电话": "phone", "电话号码": "phone", "手机号": "phone", "手机号码": "phone",
    "邮箱": "email", "电子邮箱": "email", "邮件地址": "email", "电子邮件地址": "email",
    "地址": "address", "住址": "address", "家庭住址": "address",
    "名字": "name", "姓名": "name", "雇主": "employer", "公司": "employer",
    "职业": "occupation", "工作": "occupation", "居住地": "location", "所在地": "location",
}
_ATTR_PATTERN = "|".join(re.escape(x) for x in sorted(_ATTRS, key=len, reverse=True))
_NAME = r"(?:the\s+)?(?:project\s+)?[A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*){0,3}?"
_SUBJECT = rf"(?:I|you|{_NAME})"
_OWNER = rf"(?:my|your|{_NAME}['’]s)"
_CN_SUBJECT = r"(?:我|你|[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_-]{0,20}?)"
_NOW = r"(?:(?:now|currently|today|actually)\s+)?"
_VALUE = r"(?P<value>.+?)"
_FLAGS = re.IGNORECASE
_UNSAFE = re.compile(
    r"\b(?:if|unless|imagine|suppose|assuming|assume|hypothetical|quote|quoted|"
    r"quotation|says|said|claims|claimed|might|maybe|perhaps|would|could|"
    r"reportedly|according|wish|hope)\b|\bfor\s+example\b|\bexample\s*:|"
    r"如果|假如|假设|设想|比如|例如|引用|据说|声称|可能|也许|希望|想象", _FLAGS,
)
_QUESTION = re.compile(r"^(?:what|which|when|where|who|why|how|do|does|did|is|are|can|please\s+(?:tell|explain))\b|^(?:请问|什么|哪个|哪里|谁|是否)", _FLAGS)
_NEGATION = re.compile(r"\b(?:not|never|no\s+longer|cannot)\b|n['’]t\b|不是|并非|不再|没有|并不|不喜欢|不偏好|不用|不住", _FLAGS)
_UPDATE = re.compile(r"\b(?:changed|updated|switched|replaced|instead|no\s+longer|anymore)\b|改为|改成|更新|换成|不再|而不是", _FLAGS)
_CLAUSE_START = rf"(?:{_OWNER}\s+(?:{_ATTR_PATTERN}|favorite|favourite|preferred)|{_SUBJECT}\s+(?:live|lives|reside|resides|work|works|like|likes|prefer|prefers|use|uses|is|am|changed|updated)|(?:我的|你的|我|你|项目))"
# CJK clauses need no word boundary between a pronoun and its attribute.
_CLAUSE_BREAK = re.compile(rf"(?:[,，]\s*(?:and\s+)?|\s+and\s+)(?=(?:{_CLAUSE_START}\b|(?:我的|你的|我|你|项目)))", _FLAGS)
# This table provides lexical category hints for common database names. It is
# not a claim that every tool use is exclusive or that a type was measured.
_DATABASE_NAMES = {"postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis", "mariadb", "oracle", "dynamodb", "neo4j"}


def _norm(value: str) -> str:
    return " ".join(value.casefold().strip().split()).strip(" ,.;:!?。；，：！？\"“”'‘’")


def _subject(value: str, role: str) -> str | None:
    value = _norm(value)
    if value in {"i", "my", "我", "我的"}:
        return "__assistant__" if role.casefold() == "assistant" else "__user__"
    if value in {"you", "your", "你", "你的"}:
        return "__user__" if role.casefold() == "assistant" else "__assistant__"
    value = re.sub(r"['’]s$|的$", "", value)
    value = re.sub(r"^(?:the\s+)?project\s+|^the\s+|^项目", "", value).strip()
    if not value or value in {"it", "he", "she", "they", "we", "someone", "this", "that"}:
        return None
    # A trailing predicate or reporting verb means the broad name pattern ate
    # an unsupported clause; do not assign that clause to a invented entity.
    if re.search(r"\b(?:not|said|says|think|believe|if|and|but|my|your)\b", value):
        return None
    return value


def _pieces(content: str) -> Iterable[tuple[str, int]]:
    # A dot inside an email, version, or abbreviation is not a sentence end.
    sentence_re = re.compile(r".+?(?:[.!?](?=\s|$)|[。！？;；\n]|$)", re.DOTALL)
    for sentence in sentence_re.finditer(content):
        source = sentence.group()
        stripped = source.strip()
        if not stripped or "?" in source or "？" in source or _UNSAFE.search(source) or _QUESTION.search(stripped):
            continue
        # Quoted/reported statements are evidence of a quote, not the speaker's
        # own assertion. Apostrophes in contractions/possessives are unaffected.
        if re.search(r'["“”`]|(?:^|\s)[\'‘].+?[\'’](?:\s|[.!?]|$)', source):
            continue
        boundaries = list(_CLAUSE_BREAK.finditer(source))
        last = 0
        for boundary in boundaries:
            yield source[last:boundary.start()], sentence.start() + last
            last = boundary.end()
        yield source[last:], sentence.start() + last


def supports_state_update(content: str) -> bool:
    """Reject quoted, hypothetical, reported or interrogative state changes.

    The legacy relation parser may still index these sources for recall; it
    must not use their hypothetical changes to invalidate a real assertion.
    """
    return not ("?" in content or "？" in content or _UNSAFE.search(content)
                or _QUESTION.search(content.strip()) or re.search(r'["“”`]|(?:^|\s)[\'‘].+?[\'’](?:\s|[.!?]|$)', content))


def extract_facts(content: str, role: str = "user") -> list[dict[str, Any]]:
    """Extract selected direct assertions, never an answer or generated prose.

    Scalar keys are name/phone/email/address/location/employer/occupation.
    Preferences and generic tool usage are multi-valued. Explicit replacement
    of such a value emits an exact-value negative plus the new positive.
    ``source_start/end`` delimit ``text`` in the untouched input.
    """
    if not isinstance(content, str) or not content.strip():
        return []
    facts: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def add(piece: str, offset: int, owner: str, predicate: str, value: str,
            *, polarity: bool = True, exclusive: bool = True, explicit: bool = False) -> None:
        subject = _subject(owner, role)
        normalized = _norm(value)
        if not subject or not normalized or len(normalized) > 240:
            return
        if _UNSAFE.search(normalized) or _NEGATION.search(normalized):
            return
        # Do not turn "phone is X and something unsupported" into one value.
        if re.search(r"\b(?:because|but|although|while)\b|但是|因为|不过", normalized):
            return
        left = len(piece) - len(piece.lstrip())
        right = len(piece.rstrip(" \t\r\n.;!?。；！？"))
        text = piece[left:right]
        if not text:
            return
        start, end = offset + left, offset + right
        key = (subject, predicate, normalized, polarity, start, end)
        if key in seen:
            return
        seen.add(key)
        facts.append({"subject": subject, "predicate": predicate, "value": normalized,
                      "text": text, "polarity": polarity, "exclusive": exclusive,
                      "explicit_update": explicit, "source_start": start, "source_end": end})

    def matched(pattern: str, statement: str) -> re.Match[str] | None:
        return re.fullmatch(pattern, statement, _FLAGS)

    for piece, offset in _pieces(content):
        statement = piece.strip().rstrip(".;!?。；！？").strip()
        statement = re.sub(r"^(?:now|currently|today|actually)\s*,?\s*|^(?:现在|目前|当前)", "", statement, flags=_FLAGS)
        if not statement:
            continue

        # Multi-valued preferences require explicit replacement, never merely
        # a later positive preference with a different value.
        m = matched(rf"(?P<owner>{_OWNER})\s+preference(?:\s+for\s+(?P<topic>[A-Za-z -]+?))?\s+(?:changed|updated)\s+from\s+(?P<old>.+?)\s+to\s+{_VALUE}", statement)
        if not m:
            m = matched(rf"(?:I|you)\s+(?:changed|updated)\s+(?P<owner>my|your)\s+preference(?:\s+for\s+(?P<topic>[A-Za-z -]+?))?\s+from\s+(?P<old>.+?)\s+to\s+{_VALUE}", statement)
        if m:
            pred = "preference:" + (_norm(m['topic']) if m['topic'] else "general")
            add(piece, offset, m['owner'], pred, m['old'], polarity=False, exclusive=False, explicit=True)
            add(piece, offset, m['owner'], pred, m['value'], exclusive=False, explicit=True)
            continue

        # Property-aware updates have a grounded key; an untyped "X changed
        # from A to B" is intentionally left to the store's prior-key resolver.
        m = matched(rf"(?P<owner>{_OWNER})\s+(?P<attr>{_ATTR_PATTERN})\s+(?:(?:has\s+been|was|is)\s+)?(?:changed|updated|switched)\s+(?:from\s+(?P<old>.+?)\s+)?to\s+{_VALUE}", statement)
        if not m:
            m = matched(rf"(?:I|you)\s+(?:changed|updated)\s+(?P<owner>my|your)\s+(?P<attr>{_ATTR_PATTERN})\s+(?:from\s+(?P<old>.+?)\s+)?to\s+{_VALUE}", statement)
        if m:
            pred = _ATTRS[m['attr'].casefold()]
            if pred.startswith("uses:") and m.groupdict().get("old"):
                add(piece, offset, m['owner'], pred, m['old'], polarity=False, exclusive=False, explicit=True)
            add(piece, offset, m['owner'], pred, m['value'], exclusive=not pred.startswith("uses:"), explicit=True)
            continue

        m = matched(rf"(?P<owner>{_OWNER})\s+(?P<attr>{_ATTR_PATTERN})\s+is\s+{_NOW}(?P<negative>not\s+)?{_VALUE}", statement)
        if m:
            pred = _ATTRS[m['attr'].casefold()]
            add(piece, offset, m['owner'], pred, m['value'], polarity=not bool(m['negative']),
                exclusive=not bool(m['negative']) and not pred.startswith("uses:"), explicit=bool(m['negative']) or bool(_UPDATE.search(statement)))
            continue

        m = matched(rf"(?P<owner>{_OWNER})\s+(?:favorite|favourite|preferred)\s+(?P<topic>[A-Za-z][A-Za-z -]{{0,35}}?)\s+is\s+{_NOW}{_VALUE}", statement)
        if m:
            add(piece, offset, m['owner'], "preference:" + _norm(m['topic']), m['value'], exclusive=False)
            continue

        # Direct identity is accepted only with an explicit name marker.
        m = matched(rf"(?P<owner>I|you)\s+(?:am|are)\s+(?:named|called)\s+{_VALUE}", statement)
        if m:
            add(piece, offset, m['owner'], "name", m['value'])
            continue

        m = matched(rf"(?P<owner>{_SUBJECT})\s+{_NOW}(?:live|lives|reside|resides)\s+in\s+(?P<location>.+?)\s+and\s+(?:work|works)\s+(?:at|for)\s+(?P<employer>.+)", statement)
        if m:
            add(piece, offset, m['owner'], "location", m['location'])
            add(piece, offset, m['owner'], "employer", m['employer'])
            continue

        m = matched(rf"(?P<owner>{_SUBJECT})\s+{_NOW}(?P<negative>no\s+longer\s+|(?:do|does)\s+not\s+|(?:don't|doesn't)\s+)?(?P<verb>live|lives|reside|resides|work|works)\s+(?:in|at|for)\s+{_VALUE}(?:\s+anymore)?", statement)
        if m:
            pred = "location" if m['verb'].casefold() in {"live", "lives", "reside", "resides"} else "employer"
            value = re.sub(r"\s+anymore$", "", m['value'], flags=_FLAGS)
            add(piece, offset, m['owner'], pred, value, polarity=not bool(m['negative']), exclusive=not bool(m['negative']), explicit=bool(m['negative']))
            continue

        m = matched(rf"(?P<owner>{_SUBJECT})\s+(?:is|am|are)\s+{_NOW}(?P<negative>not\s+)?(?:located\s+)?in\s+{_VALUE}", statement)
        if m:
            add(piece, offset, m['owner'], "location", m['value'], polarity=not bool(m['negative']), exclusive=not bool(m['negative']), explicit=bool(m['negative']))
            continue

        m = matched(rf"(?P<owner>{_SUBJECT})\s+{_NOW}(?P<negative>no\s+longer\s+|(?:do|does)\s+not\s+|(?:don't|doesn't)\s+)?(?P<verb>like|likes|prefer|prefers|use|uses)\s+{_VALUE}", statement)
        if m:
            value = re.sub(r"\s+anymore$", "", m['value'], flags=_FLAGS)
            using = m['verb'].casefold() in {"use", "uses"}
            topic = "general"
            if using:
                typed = re.fullmatch(r"(?:a\s+|the\s+)?(?P<topic>database|editor|browser|language|framework)\s+(?P<value>.+)|(?P<suffix_value>.+?)\s+(?P<suffix_topic>database|editor|browser|language|framework)", value, _FLAGS)
                if typed:
                    topic = (typed['topic'] or typed['suffix_topic']).casefold()
                    value = typed['value'] or typed['suffix_value']
                elif _norm(value) in _DATABASE_NAMES:
                    topic = "database"
            pred = ("uses:" if using else "preference:") + topic
            replacement = re.fullmatch(r"(?P<new>.+?)\s+(?:instead\s+of|rather\s+than)\s+(?P<old>.+)", value, _FLAGS)
            if replacement and not m['negative']:
                add(piece, offset, m['owner'], pred, replacement['old'], polarity=False, exclusive=False, explicit=True)
                add(piece, offset, m['owner'], pred, replacement['new'], exclusive=False, explicit=True)
            else:
                add(piece, offset, m['owner'], pred, value, polarity=not bool(m['negative']), exclusive=False, explicit=bool(m['negative']))
            continue

        # "Alice is a doctor" is grounded occupation; arbitrary adjectives are
        # not accepted as either names or occupations.
        m = matched(rf"(?P<owner>{_SUBJECT})\s+(?:is|am|are)\s+(?:a|an)\s+(?P<value>doctor|teacher|engineer|researcher|student|nurse|designer|developer|lawyer|manager|professor|accountant)", statement)
        if m:
            add(piece, offset, m['owner'], "occupation", m['value'])
            continue

        m = matched(rf"(?P<owner>{_CN_SUBJECT})(?:的)?偏好(?:从|由)(?P<old>.+?)(?:改为|改成|更新为|换成)(?P<value>.+)", statement)
        if m:
            add(piece, offset, m['owner'], "preference:general", m['old'], polarity=False, exclusive=False, explicit=True)
            add(piece, offset, m['owner'], "preference:general", m['value'], exclusive=False, explicit=True)
            continue

        m = matched(rf"(?P<owner>{_CN_SUBJECT})(?:的)?(?P<attr>{_ATTR_PATTERN})(?:已|已经)?(?:从|由)(?P<old>.+?)(?:改为|改成|更新为|换成)(?P<value>.+)", statement)
        if not m:
            m = matched(rf"(?P<owner>{_CN_SUBJECT})(?:的)?(?P<attr>{_ATTR_PATTERN})(?:已|已经)?(?:改为|改成|更新为|换成)(?P<value>.+)", statement)
        if m:
            pred = _ATTRS[m['attr']]
            if pred.startswith("uses:") and m.groupdict().get("old"):
                add(piece, offset, m['owner'], pred, m['old'], polarity=False, exclusive=False, explicit=True)
            add(piece, offset, m['owner'], pred, m['value'], exclusive=not pred.startswith("uses:"), explicit=True)
            continue

        m = matched(rf"(?P<owner>{_CN_SUBJECT})(?:的)?(?P<attr>{_ATTR_PATTERN})(?:现在|目前|当前)?(?P<negative>不是|并非|不再是|是)(?P<value>.+)", statement)
        if m:
            pred = _ATTRS[m['attr']]
            negative = m['negative'] != "是"
            add(piece, offset, m['owner'], pred, m['value'], polarity=not negative, exclusive=not negative and not pred.startswith("uses:"), explicit=negative)
            continue

        m = matched(rf"(?P<owner>{_CN_SUBJECT})(?:现在|目前|当前)?(?P<negative>不再|不)?(?P<verb>喜欢|偏好|使用|用|住在|居住在|在)(?P<value>.+)", statement)
        if m:
            verb, value = m['verb'], m['value']
            if verb == "在":
                work = re.fullmatch(r"(.+?)(?:工作|上班|任职)", value)
                if not work:
                    continue
                pred, value = "employer", work[1]
            elif verb in {"住在", "居住在"}:
                pred = "location"
            elif verb in {"使用", "用"}:
                pred = "uses:general"
                if value.startswith("数据库"):
                    pred, value = "uses:database", value[3:]
                elif value.endswith("数据库"):
                    pred, value = "uses:database", value[:-3]
                elif _norm(value) in _DATABASE_NAMES:
                    pred = "uses:database"
            else:
                pred = "preference:general"
            replacement = re.fullmatch(r"(.+?)(?:而不是|替代)(.+)", value)
            exclusive = not bool(m['negative']) and pred in {"location", "employer"}
            if replacement and not m['negative']:
                add(piece, offset, m['owner'], pred, replacement[2], polarity=False, exclusive=False, explicit=True)
                add(piece, offset, m['owner'], pred, replacement[1], exclusive=exclusive, explicit=True)
            else:
                add(piece, offset, m['owner'], pred, value, polarity=not bool(m['negative']), exclusive=exclusive, explicit=bool(m['negative']))
            continue

        m = matched(rf"(?P<owner>{_CN_SUBJECT})(?:现在)?叫(?P<value>.+)", statement)
        if m:
            add(piece, offset, m['owner'], "name", m['value'])

    return sorted(facts, key=lambda item: (item['source_start'], item['source_end']))


def fact_index_terms(facts: Iterable[dict[str, Any]]) -> list[str]:
    """Return lexical search hints; these are never source evidence or answers."""
    aliases = {
        "phone": ("phone", "telephone", "mobile", "number", "电话", "手机号"),
        "email": ("email", "mail", "邮箱"), "name": ("name", "called", "姓名", "名字"),
        "address": ("address", "住址", "地址"),
        "location": ("location", "place", "live", "reside", "居住", "住在"),
        "employer": ("employer", "company", "work", "公司", "工作"),
        "occupation": ("occupation", "job", "职业"),
        "preference": ("preference", "prefer", "like", "喜欢", "偏好"),
        "uses": ("use", "uses", "使用"),
    }
    values: list[str] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        predicate = str(fact.get("predicate", ""))
        base, _, topic = predicate.partition(":")
        values.extend(aliases.get(base, ()))
        if topic and topic != "general":
            values.append(topic)
        for key in ("subject", "value"):
            value = str(fact.get(key, ""))
            if value and not value.startswith("__"):
                values.append(value)
    return list(dict.fromkeys(values))
