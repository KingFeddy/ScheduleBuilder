"""Recognize explicit special-topic titles without treating honors as topics."""
import re

_TOPIC_PREFIX = re.compile(r"^(?:ST\s*[:–—-]\s*|(?:special|selected)\s+topics\b\s*[:–—-]?\s*)", re.I)


def clean_section_title(value: object) -> str | None:
    # PostgreSQL text cannot store NUL bytes or unpaired Unicode surrogates.
    # Treat a bad title as missing so valid section/credit updates can proceed.
    if (not isinstance(value, str) or "\x00" in value
            or any(0xD800 <= ord(character) <= 0xDFFF for character in value)):
        return None
    title = " ".join(value.split())
    return title if title and len(title) <= 512 else None


def is_topic_title(title: str | None) -> bool:
    return bool(title and _TOPIC_PREFIX.match(title))


def topic_label(title: str | None, section_number: str | None, crn: str) -> str:
    label = _TOPIC_PREFIX.sub("", title or "").strip()
    # An unnamed section must not be interchangeable with a named topic.
    return label or f"Topic not listed (section {section_number or crn}, CRN {crn})"


def topic_key(label: str) -> str:
    return " ".join(label.split()).casefold()
