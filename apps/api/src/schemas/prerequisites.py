"""Versioned storage models for observed prerequisite rules and source evidence.

These describe reported conditions; they do not establish a student's eligibility.
In particular, omitted concurrency information stays unspecified.
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StoredModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CourseCondition(StoredModel):
    kind: Literal["course"] = "course"
    course_code: str = Field(min_length=2)
    minimum_grade: str | None
    level: str | None
    timing: Literal["prior", "prior_or_concurrent", "concurrent", "unspecified"]
    required_crn: str | None = None
    source_row: int = Field(ge=1)


class UnresolvedCondition(StoredModel):
    kind: Literal["unresolved"] = "unresolved"
    reason: str
    source_row: int | None = Field(default=None, ge=1)


class AllConditions(StoredModel):
    kind: Literal["all"] = "all"
    # An empty all-group explicitly represents verified absence of conditions.
    items: list[Rule]


class AnyConditions(StoredModel):
    kind: Literal["any"] = "any"
    items: list[Rule] = Field(min_length=2)


Rule = Annotated[
    CourseCondition | UnresolvedCondition | AllConditions | AnyConditions,
    Field(discriminator="kind"),
]
AllConditions.model_rebuild()
AnyConditions.model_rebuild()


class ObservationScope(StoredModel):
    term: str
    crn: str


class PrerequisiteRules(StoredModel):
    schema_version: Literal[1] = 1
    scope: ObservationScope
    prerequisites: Rule
    corequisites: Rule


class SourceEvidence(StoredModel):
    kind: Literal["subjects", "prerequisites", "corequisites"]
    url: str
    term: str
    crn: str | None
    fetched_at: datetime
    http_status: int | None
    content_type: str | None
    body: str | None
    body_encoding: Literal["text", "base64"] = "text"
    sha256: str | None
    truncated: bool = False

    @property
    def text(self) -> str | None:
        """Recover evidence that needed encoding because JSONB cannot store NUL."""
        if self.body is None or self.body_encoding == "text":
            return self.body
        return base64.b64decode(self.body, validate=True).decode("utf-8")


class SubjectLookup(StoredModel):
    mapping: dict[str, str]
    source: SourceEvidence


class PrerequisiteRefresh(StoredModel):
    rules: PrerequisiteRules | None
    sources: list[SourceEvidence]
    status: Literal["verified", "verified_empty", "failed", "unresolved"]
    error: str | None = None


def has_unresolved(rule: Rule) -> bool:
    if isinstance(rule, UnresolvedCondition):
        return True
    if isinstance(rule, (AllConditions, AnyConditions)):
        return any(has_unresolved(child) for child in rule.items)
    return False


def is_empty(rule: Rule) -> bool:
    return isinstance(rule, AllConditions) and not rule.items


def legacy_course_codes(rules: PrerequisiteRules) -> list[str] | None:
    """Keep the existing AND-only consumer working without flattening alternatives.

    None means the legacy column cannot express this rule set and must be retained
    as historical, unverified data. Grade/level fields live only in the full tree.
    """
    if not is_empty(rules.corequisites):
        return None

    def visit(rule: Rule) -> list[str] | None:
        if isinstance(rule, CourseCondition):
            return [rule.course_code] if rule.timing in {"prior", "unspecified"} else None
        if isinstance(rule, AllConditions):
            codes = []
            for child in rule.items:
                child_codes = visit(child)
                if child_codes is None:
                    return None
                codes.extend(child_codes)
            return codes
        return None

    return visit(rules.prerequisites)
