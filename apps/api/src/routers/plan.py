from __future__ import annotations

import base64
import hashlib
import logging
from collections import defaultdict
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.schemas.plan import ParseValidationError, ParsedDegreeValidated
from src.services.dw_parser import parse_degree_works_regex
from src.services.plan import generate_plan, validate_parsed_degree

# Single shared limiter defined once in main.py — never instantiate a second one here
from main import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plan"])

MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_PDF_BYTES = 5 * 1024          # 5 KB — DegreeWorks PDFs are never this small
PDF_MAGIC = b"%PDF-"

GER_PREFIXES = ["COM", "ENG", "HUM", "HIST", "PHIL", "PSYC", "SOC", "STS", "ARH", "MUS"]


class ParseRequest(BaseModel):
    pdf_base64: str
    client_pdf_hash: str  # client-computed SHA-256 hex — informational only, not trusted


class ParseResponse(BaseModel):
    parsed: dict               # ParsedDegreeValidated as dict
    server_hash: str           # authoritative hash, computed server-side from raw bytes
    warnings: list[str] = []


@router.post("/api/plan/parse", response_model=ParseResponse)
@limiter.limit("5/minute")
async def parse_degree_works(request: Request, body: ParseRequest) -> ParseResponse:
    """
    Stateless PDF parsing endpoint. Receives a base64-encoded DegreeWorks PDF,
    extracts structured degree data, and returns it as JSON. Nothing is stored
    server-side — the client persists the result to localStorage.
    """
    # 1. Decode base64
    try:
        pdf_bytes = base64.b64decode(body.pdf_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 encoding.")

    # 2. Size guards — before any CPU work
    if len(pdf_bytes) < MIN_PDF_BYTES:
        raise HTTPException(
            status_code=400,
            detail="File too small to be a DegreeWorks PDF.",
        )
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File too large. Maximum size is 5 MB.",
        )

    # 3. Magic bytes — must be a real PDF, not a JPEG or Word doc
    if not pdf_bytes.startswith(PDF_MAGIC):
        raise HTTPException(
            status_code=400,
            detail=(
                "File does not appear to be a PDF. "
                "Please upload your DegreeWorks degree audit."
            ),
        )

    # 4. Server-side hash — authoritative; client hash is informational only.
    # Client stores against server_hash so two identical PDFs always hit the
    # same localStorage key regardless of how the client encoded them.
    server_hash = hashlib.sha256(pdf_bytes).hexdigest()

    if body.client_pdf_hash and body.client_pdf_hash != server_hash:
        logger.warning(
            "hash mismatch: client=%s server=%s",
            body.client_pdf_hash[:8],
            server_hash[:8],
        )

    # 5. Parse — structural text extraction + regex
    try:
        raw_parsed = parse_degree_works_regex(pdf_bytes)
    except ValueError as e:
        logger.error("PDF parse failed: %s", e)
        raise HTTPException(status_code=422, detail=str(e))

    # 6. Business-logic validation — the trust boundary.
    # Only a ParsedDegreeValidated is returned; raw ParsedDegree is never sent.
    try:
        validated: ParsedDegreeValidated = validate_parsed_degree(raw_parsed)
    except ParseValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"The extracted data appears inconsistent ({e.field}): {e.message} "
                f"Please verify this is a DegreeWorks degree audit PDF."
            ),
        )

    # 7. Non-fatal warnings — surfaced to the user but do not block the response
    warnings: list[str] = []
    if (
        validated.credits_remaining is not None
        and validated.credits_remaining > 15
        and len(validated.still_needed) < 2
    ):
        warnings.append(
            "Fewer requirements were found than expected. "
            "If you have a double major or minor, verify the plan is complete."
        )
    if validated.catalog_year and validated.catalog_year < 2021:
        warnings.append(
            f"Your catalog year ({validated.catalog_year}) uses an older curriculum. "
            f"Verify GER requirements with your advisor."
        )

    # 8. Return — nothing stored server-side
    return ParseResponse(
        parsed=validated.model_dump(),
        server_hash=server_hash,
        warnings=warnings,
    )


# ── POST /api/plan/generate ───────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    parsed_degree: dict     # ParsedDegreeValidated serialized to JSON by the client
    preferences: dict       # {courses: list[str], credits_per_semester: int}


@router.post("/api/plan/generate")
@limiter.limit("10/minute")
async def generate_degree_plan(
    request: Request,
    body: GenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Stateless plan generation. Accepts a ParsedDegreeValidated (from /api/plan/parse)
    and student preferences, returns a semester-by-semester plan.
    Nothing is stored server-side — the client persists the result to localStorage.
    """
    try:
        validated = ParsedDegreeValidated.model_validate(body.parsed_degree)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid parsed degree data: {e}")

    try:
        plan = await generate_plan(validated, body.preferences, db)
    except ParseValidationError as e:
        raise HTTPException(status_code=422, detail=f"{e.field}: {e.message}")

    return asdict(plan)


@router.get("/api/plan/ger-courses")
async def ger_courses(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            SELECT
                SUBSTRING(course_code FROM '^[A-Z]+') AS prefix,
                course_code,
                title
            FROM courses
            WHERE SUBSTRING(course_code FROM '^[A-Z]+') = ANY(:prefixes)
            ORDER BY course_code
        """),
        {"prefixes": GER_PREFIXES},
    )
    rows = result.mappings().all()

    groups: defaultdict[str, list] = defaultdict(list)
    for row in rows:
        groups[row["prefix"]].append({"code": row["course_code"], "title": row["title"]})

    return {
        "groups": [
            {"prefix": p, "courses": groups[p]}
            for p in GER_PREFIXES
            if groups.get(p)
        ]
    }
