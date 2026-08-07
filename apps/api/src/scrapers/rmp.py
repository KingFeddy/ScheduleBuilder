from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .lock import advisory_lock, RMP_SCRAPER_LOCK_ID

logger = logging.getLogger(__name__)

RMP_TTL_HOURS = 24
RMP_ENDPOINT  = "https://www.ratemyprofessors.com/graphql"
RMP_SCHOOL_ID = "U2Nob29sLTY2OA=="  # NJIT School-668

RMP_QUERY = """
query TeacherSearchQuery($query: TeacherSearchQuery!) {
  newSearch {
    teachers(query: $query, first: 1) {
      edges {
        node {
          firstName lastName
          avgRating avgDifficulty wouldTakeAgainPercent
          numRatings
          teacherRatingTags { tagName tagCount }
        }
      }
    }
  }
}
"""

RMP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Authorization":  "Basic dGVzdDp0ZXN0",
    "Origin":         "https://www.ratemyprofessors.com",
    "Referer":        "https://www.ratemyprofessors.com/",
    "Content-Type":   "application/json",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class RMPAuthError(Exception):
    """RMP returned 401 — authorization credential may have changed."""


class RMPSchemaError(Exception):
    """RMP response structure is unexpected — GraphQL schema may have changed."""


class RMPRateLimitError(Exception):
    """RMP returned 429 after all retries."""


def _name_tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z']+", s.lower()))


def _rmp_match_is_plausible(professor_name: str, rmp_last_name: str) -> bool:
    """
    RMP's search is a loose fuzzy match — it can return a completely
    unrelated professor sharing no more than a first name with the query
    (e.g. searching "Ariel Tang" returned "Ariel Sykes", an unrelated
    Humanities professor with no connection to the Accounting professor
    being searched for). Require at least one token of Banner's last name
    to appear in RMP's own lastName field before trusting the match —
    otherwise a wrong professor's rating gets silently stored and shown
    as fact.
    """
    banner_last = professor_name.split(",", 1)[0] if "," in professor_name else professor_name
    return bool(_name_tokens(banner_last) & _name_tokens(rmp_last_name))


async def _get_cached_rmp(
    session: AsyncSession,
    professor_name: str,
) -> dict | bool | None:
    """
    Returns:
      dict  — valid cached data
      False — cached as not-found on RMP
      None  — no entry or expired
    """
    result = await session.execute(
        text("""
            SELECT rmp_data, expires_at
            FROM rmp_cache
            WHERE professor_name = :name
        """),
        {"name": professor_name},
    )
    row = result.mappings().first()

    if not row:
        return None

    if datetime.now(timezone.utc) > row["expires_at"]:
        return None

    if row["rmp_data"] is None:
        return False

    data = row["rmp_data"]
    return json.loads(data) if isinstance(data, str) else data


async def _set_cached_rmp(
    session: AsyncSession,
    professor_name: str,
    data: dict | None,  # None = not found on RMP
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=RMP_TTL_HOURS)

    await session.execute(
        text("""
            INSERT INTO rmp_cache (professor_name, rmp_data, cached_at, expires_at)
            VALUES (:name, :data, NOW(), :expires)
            ON CONFLICT (professor_name) DO UPDATE
            SET rmp_data   = EXCLUDED.rmp_data,
                cached_at  = NOW(),
                expires_at = EXCLUDED.expires_at
        """),
        {
            "name":    professor_name,
            "data":    json.dumps(data) if data is not None else None,
            "expires": expires_at,
        },
    )


async def fetch_rmp_rating(
    session: AsyncSession,
    professor_name: str,
    http_client: httpx.AsyncClient,
) -> dict | None:
    """
    Returns RMP data dict, or None if the professor isn't on RMP.
    Raises RMPAuthError, RMPSchemaError, or RMPRateLimitError on non-retriable failures.
    Checks Postgres cache before hitting RMP.
    """
    cached = await _get_cached_rmp(session, professor_name)
    if cached is not None:
        return None if cached is False else cached

    # Banner names are "Last, First" — normalize to "First Last" for RMP search
    if "," in professor_name:
        last, first = professor_name.split(",", 1)
        search_name = f"{first.strip()} {last.strip()}"
    else:
        search_name = professor_name

    await asyncio.sleep(3 + random.uniform(0, 2))

    RETRY_DELAYS = [10, 30, 60]

    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay > 0:
            logger.info("RMP/%s: retry in %ds (attempt %d)", professor_name, delay, attempt + 1)
            await asyncio.sleep(delay)

        try:
            response = await http_client.post(
                RMP_ENDPOINT,
                json={
                    "query": RMP_QUERY,
                    "variables": {
                        "query": {"text": search_name, "schoolID": RMP_SCHOOL_ID}
                    },
                },
                headers=RMP_HEADERS,
                timeout=httpx.Timeout(15.0),
            )

            if response.status_code == 401:
                raise RMPAuthError(
                    f"RMP returned 401 — authorization credential may have changed. "
                    f"Update RMP_HEADERS in rmp.py."
                )

            if response.status_code == 429:
                if attempt < len(RETRY_DELAYS):
                    logger.warning("RMP/%s: rate limited, retrying", professor_name)
                    continue
                raise RMPRateLimitError(
                    f"RMP rate limit exceeded after {len(RETRY_DELAYS) + 1} attempts"
                )

            if response.status_code != 200:
                logger.warning(
                    "RMP/%s: unexpected status %d", professor_name, response.status_code
                )
                if attempt < len(RETRY_DELAYS):
                    continue
                return None

            data = response.json()

            try:
                edges = data["data"]["newSearch"]["teachers"]["edges"]
            except (KeyError, TypeError) as e:
                raise RMPSchemaError(
                    f"RMP response missing expected keys: {e}. "
                    f"GraphQL schema may have changed."
                )

            if not edges:
                await _set_cached_rmp(session, professor_name, None)
                return None

            node = edges[0]["node"]

            if not _rmp_match_is_plausible(professor_name, node.get("lastName", "")):
                logger.info(
                    "RMP/%s: top search result %r %r doesn't match — treating as not found",
                    professor_name, node.get("firstName"), node.get("lastName"),
                )
                await _set_cached_rmp(session, professor_name, None)
                return None

            result = {
                "name":                 f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
                "rmp_score":            node.get("avgRating"),
                "rmp_difficulty":       node.get("avgDifficulty"),
                "rmp_num_ratings":      node.get("numRatings"),
                "rmp_would_take_again": node.get("wouldTakeAgainPercent"),
                "rmp_tags": [
                    t["tagName"]
                    for t in (node.get("teacherRatingTags") or [])
                    if t.get("tagCount", 0) > 0
                ],
            }

            await _set_cached_rmp(session, professor_name, result)
            return result

        except (RMPAuthError, RMPSchemaError, RMPRateLimitError):
            raise
        except httpx.TimeoutException:
            logger.warning("RMP/%s: timeout on attempt %d", professor_name, attempt + 1)
            if attempt >= len(RETRY_DELAYS):
                logger.error("RMP/%s: all retries exhausted", professor_name)
                return None
        except Exception as e:
            logger.error("RMP/%s: unexpected error: %s", professor_name, e, exc_info=True)
            if attempt >= len(RETRY_DELAYS):
                return None

    return None


async def run_rmp_scrape(session: AsyncSession, term: str) -> None:
    """
    Refresh RMP data for all professors with sections in the given term.
    Uses advisory lock to prevent concurrent runs.
    Skips professors already cached and not yet expired.
    """
    async with advisory_lock(session, RMP_SCRAPER_LOCK_ID, "rmp") as acquired:
        if not acquired:
            logger.info("RMP scrape: lock held — skipping this cycle")
            return

        result = await session.execute(
            text("""
                SELECT DISTINCT professor_name
                FROM sections
                WHERE term = :term AND professor_name IS NOT NULL
            """),
            {"term": term},
        )
        professors = [row[0] for row in result.fetchall()]
        logger.info("RMP scrape: %d professors to check", len(professors))

        async with httpx.AsyncClient() as client:
            for name in professors:
                try:
                    await fetch_rmp_rating(session, name, client)
                except RMPAuthError as e:
                    logger.error("RMP auth failed — stopping RMP scrape: %s", e)
                    break
                except RMPSchemaError as e:
                    logger.error("RMP schema changed — stopping RMP scrape: %s", e)
                    break
                except Exception as e:
                    logger.warning("RMP/%s: failed — %s", name, e)

        await session.commit()
        logger.info("RMP scrape complete")
