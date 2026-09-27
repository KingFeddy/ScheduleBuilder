"""Run bounded virtual-user stages against an owned local API and disposable DB."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import random
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]


def summarize(samples):
    elapsed = sorted(row[1] for row in samples)
    def percentile(p):
        return round(elapsed[max(0, math.ceil(len(elapsed) * p) - 1)], 1) if elapsed else None
    return {
        "requests": len(samples), "p50_ms": percentile(.5), "p95_ms": percentile(.95),
        "p99_ms": percentile(.99), "failures": sum(row[3] for row in samples),
        "statuses": dict(Counter(str(row[2]) for row in samples)),
    }


async def run_stage(client, users, seconds):
    started = time.perf_counter()
    deadline = started + seconds
    samples = []
    solved = []
    completed = 0

    async def request(kind, method, path, **kwargs):
        before = time.perf_counter()
        status, failed, data = "transport_error", True, None
        try:
            response = await client.request(method, path, **kwargs)
            status = response.status_code
            if response.is_success:
                data = response.json()
                failed = False
                if kind == "solve":
                    results = data.get("results", [])
                    expected = set(kwargs["json"]["course_codes"])
                    failed = not results or any(
                        {s["course_code"] for s in result["sections"]} != expected
                        for result in results
                    )
                    solved.append({"count": len(results), "truncated": data.get("truncated")})
                elif kind in {"search", "sections"}:
                    failed = not isinstance(data, list) or not data
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            failed = True
        samples.append((kind, (time.perf_counter() - before) * 1000, status, failed))
        return None if failed else data

    async def student(number):
        nonlocal completed
        rng = random.Random(number)
        # Spread arrivals so every stage is not an artificial synchronized burst.
        await asyncio.sleep(rng.uniform(0, min(3, seconds / 4)))
        while time.perf_counter() < deadline:
            await request("terms", "GET", "/api/terms")
            count = rng.randint(4, 6)
            start = rng.randrange(100, 394)
            subject = rng.choice(["CS", "MATH", "PHYS", "IT"])
            courses = [f"{subject}{start + n}" for n in range(count)]
            cached_professors = set()
            for code in courses:
                if time.perf_counter() >= deadline:
                    return
                await request("search", "GET", "/api/courses", params={"q": code, "term": "202690", "limit": 8})
                sections = await request("sections", "GET", f"/api/courses/{code}/sections", params={"term": "202690"})
                names = {s["professor_name"] for s in (sections or []) if s.get("professor_name")}
                await asyncio.gather(*(request("professors", "GET", f"/api/professors/{name}")
                                       for name in names - cached_professors))
                cached_professors.update(names)
                await asyncio.sleep(rng.uniform(1, 2))
            if time.perf_counter() >= deadline:
                return
            await request("solve", "POST", "/api/schedule/solve", json={
                "course_codes": courses, "term": "202690", "professor_preferences": {},
                "options": {"hide_full_sections": rng.choice([True, False]), "minimize_gaps": True},
            })
            completed += 1
            await asyncio.sleep(rng.uniform(3, 6))

    await asyncio.gather(*(student(n) for n in range(users)))
    elapsed = time.perf_counter() - started
    groups = defaultdict(list)
    for sample in samples:
        groups[sample[0]].append(sample)
    return {"users": users, "active_seconds": seconds, "elapsed_with_drain_seconds": round(elapsed, 2),
            "completed_journeys": completed, "requests_per_second": round(len(samples) / elapsed, 2),
            **summarize(samples), "endpoints": {key: summarize(rows) for key, rows in groups.items()},
            "solve_result_counts": dict(Counter(s["count"] for s in solved)),
            "truncated_solves": sum(bool(s["truncated"]) for s in solved)}


async def exercise(base_url, token, process, users, seconds, output):
    async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False,
                                limits=httpx.Limits(max_connections=max(users) * 8,
                                                   max_keepalive_connections=max(users) * 4)) as client:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError("Local load-test API exited; see api.log in the report folder.")
            try:
                response = await client.get("/__load_test")
                if response.status_code == 200 and response.json().get("token") == token:
                    break
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(.5)
        else:
            raise RuntimeError("Owned test API did not become ready.")
        report = {"created_at": datetime.now(timezone.utc).isoformat(),
                  "environment": {"platform": platform.platform(), "logical_cpus": os.cpu_count(),
                                  "api_workers": 1, "synthetic_courses": 1200, "synthetic_sections": 6000,
                                  "ip_rate_limits": "disabled only in isolated test app"},
                  "limitations": ["Local API benchmark, not Railway capacity", "No browser rendering or Vercel proxy",
                                  "Synthetic conflict-light schedules; no PDF uploads or degree planning",
                                  "Client and API share this computer; short stages are not a sustained-load test"],
                  "stages": []}
        for count in users:
            print(f"Running {count} virtual users for {seconds}s...", flush=True)
            stage = await run_stage(client, count, seconds)
            report["stages"].append(stage)
            output.write_text(json.dumps(report, indent=2) + "\n")
            solve_p95 = stage["endpoints"].get("solve", {}).get("p95_ms")
            print(f"  {stage['requests']} requests, {stage['failures']} failures, "
                  f"p95 {stage['p95_ms']} ms; solve p95 {solve_p95} ms", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", default="10,25,50,100", help="Comma-separated stages, each 1–200 users")
    parser.add_argument("--seconds", type=int, default=30, help="Active seconds per stage, 15–300")
    args = parser.parse_args()
    try:
        users = [int(value) for value in args.users.split(",")]
    except ValueError:
        parser.error("--users must contain integers")
    if not 1 <= len(users) <= 8 or any(not 1 <= n <= 200 for n in users) or not 15 <= args.seconds <= 300:
        parser.error("Use 1–8 stages of 1–200 users, and 15–300 seconds per stage")
    try:
        subprocess.run(["docker", "info"], check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        parser.exit(1, "Start Docker Desktop, wait until it is ready, then rerun this command.\n")
    token = uuid4().hex
    folder = Path(tempfile.mkdtemp(prefix="schedule-builder-load-"))
    compose = ["docker", "compose", "-p", f"schedule-load-{token[:10]}", "-f", str(REPO_ROOT / "compose.loadtest.yml")]
    process = None
    print(f"Reports and logs: {folder}", flush=True)
    try:
        subprocess.run(compose + ["up", "-d", "--wait"], check=True, timeout=180)
        address = subprocess.check_output(compose + ["port", "postgres", "5432"], text=True).strip()
        host, db_port = address.rsplit(":", 1)
        if host != "127.0.0.1" or not db_port.isdigit():
            raise RuntimeError("Test database must be bound to loopback")
        env = {key: value for key, value in os.environ.items() if key not in {
            "DATABASE_URL", "MIGRATION_DATABASE_URL", "WEB_CONCURRENCY", "UVICORN_WORKERS"}}
        env.update(APP_ENV="test", TEST_DATABASE_URL=f"postgresql+asyncpg://njit_test:test-only@127.0.0.1:{db_port}/njit_test",
                   LOAD_TEST_TOKEN=token)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            api_port = sock.getsockname()[1]
        with (folder / "api.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "scripts.load_test_app:create_app",
                                        "--factory", "--host", "127.0.0.1", "--port", str(api_port),
                                        "--workers", "1", "--no-access-log"], cwd=API_ROOT, env=env,
                                       stdout=log, stderr=subprocess.STDOUT)
            asyncio.run(exercise(f"http://127.0.0.1:{api_port}", token, process, users, args.seconds, folder / "report.json"))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        subprocess.run(compose + ["down", "--volumes"], check=False, timeout=60)
    print(f"Finished. Report: {folder / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
