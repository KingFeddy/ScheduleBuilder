"""Shared term-code format. The runtime default lives only in Settings."""
from typing import Annotated

from pydantic import Field

TERM_CODE_PATTERN = r"^[0-9]{4}(10|50|90)$"
TermCode = Annotated[str, Field(pattern=TERM_CODE_PATTERN)]
