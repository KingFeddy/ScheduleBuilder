"""Catalog scope defaults, not a registry of valid NJIT courses or degrees.

Include the supported subjects checked in the September 2026 Banner refresh,
while retaining historical collection subjects. Aliases such as PSY/PSYC remain
distinct; this default list does not establish offerings or degree eligibility.
"""
import re

DEFAULT_CATALOG_SUBJECTS = (
    "ACCT,AD,ARCH,ARH,AS,BDS,BIOL,BME,BMET,BNFO,CE,CET,CHE,CHEM,CIM,CMT,COM,CS,"
    "DD,DS,ECE,ECET,ECON,EM,ENE,ENG,ENGL,ENGR,ENTR,EPS,ESC,ET,EVSC,FED,FIN,FRSC,"
    "FYS,GEN,HIST,HRM,HSS,HUM,ID,IE,IET,INT,INTD,IS,IT,LIB,LIT,MARC,MATH,ME,MECH,"
    "MET,MGMT,MIS,MNE,MNET,MR,MRKT,MTEN,MTSE,MUS,OM,OPSE,PE,PHEN,PHIL,PHYS,PSY,"
    "PSYC,PTC,RBHS,RBTS,SDET,SET,SOC,SSC,STS,THTR,TRAN,TUTR,USYS,YWCC"
)
# Broad browsing categories only. Membership does not establish GER eligibility.
DEFAULT_GER_SUBJECTS = "COM,ENG,HUM,HIST,PHIL,PSYC,SOC,STS,ARH,MUS,LIB,SSC"


def course_subject(code: str) -> str | None:
    """Extract subjects from concrete or normalized wildcard course options."""
    match = re.fullmatch(r"([A-Z]{2,5})(?:\d[\dX]{2}[A-Z]?|X{3})", code.strip().upper())
    return match[1] if match else None
