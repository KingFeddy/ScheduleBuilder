"""Catalog scope defaults, not a registry of valid NJIT courses or degrees.

Retain the original collection subjects and cover the subjects referenced by
our existing synthetic parser/planner fixtures and elective browsers. Aliases
such as PSY/PSYC remain distinct; Banner subject discovery is a separate concern.
"""
import re

DEFAULT_CATALOG_SUBJECTS = (
    "ACCT,ARH,CHEM,COM,CS,ECE,ENG,FIN,FRSC,HIST,HSS,HUM,IS,IT,LIB,LIT,"
    "MATH,MUS,PHIL,PHYS,PSY,PSYC,SDET,SOC,SSC,STS,THTR,YWCC"
)
# Broad browsing categories only. Membership does not establish GER eligibility.
DEFAULT_GER_SUBJECTS = "COM,ENG,HUM,HIST,PHIL,PSYC,SOC,STS,ARH,MUS,LIB,SSC"


def course_subject(code: str) -> str | None:
    """Extract subjects from concrete or normalized wildcard course options."""
    match = re.fullmatch(r"([A-Z]{2,5})(?:\d[\dX]{2}[A-Z]?|X{3})", code.strip().upper())
    return match[1] if match else None
