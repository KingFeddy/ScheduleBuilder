from pydantic import BaseModel

from src.terms import TermCode


class TermOption(BaseModel):
    code: TermCode
    label: str
    has_data: bool


class TermsResponse(BaseModel):
    default_term: TermCode
    terms: list[TermOption]
