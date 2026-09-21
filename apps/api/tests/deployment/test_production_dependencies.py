"""Exercise deployed commands in a separate environment with no dev packages.

Install the clean environment from the lockfile (using uv's cache when present).
All subsequent startup commands run offline. Copy only runtime sources; never
copy the application's .env or local virtualenv.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from tests.database import UNCONFIGURED_DATABASE_URL


API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]
DEV_PACKAGES = {"pytest", "pytest-asyncio", "hypothesis"}


def docker_command(filename):
    lines = (API_ROOT / filename).read_text().splitlines()
    return json.loads(next(line[4:] for line in reversed(lines) if line.startswith("CMD ")))


def railway_config(path):
    return tomllib.loads(path.read_text())


API_COMMANDS = [
    pytest.param(docker_command("Dockerfile"), id="docker-api"),
    pytest.param(
        ["sh", "-c", railway_config(REPO_ROOT / "railway.toml")["services"][0]["deploy"]["startCommand"]],
        id="railway-api",
    ),
]
SCRAPER_COMMANDS = [
    pytest.param(docker_command("Dockerfile.scraper"), id="docker-scraper"),
    pytest.param(
        shlex.split(railway_config(API_ROOT / "railway.scraper.toml")["deploy"]["startCommand"]),
        id="railway-scraper",
    ),
    pytest.param(
        shlex.split(railway_config(REPO_ROOT / "railway.toml")["services"][1]["cron"]["command"]),
        id="railway-cron",
    ),
]


@pytest.fixture(scope="module")
def runtime_copy(tmp_path_factory):
    project = tmp_path_factory.mktemp("production-runtime")
    for filename in ("pyproject.toml", "uv.lock", "main.py"):
        shutil.copy2(API_ROOT / filename, project / filename)
    for directory in ("src", "scripts", "migrations"):
        shutil.copytree(API_ROOT / directory, project / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return project


@pytest.fixture(scope="module")
def production_runtime(runtime_copy):
    uv = shutil.which("uv")
    assert uv, "Install uv and run uv sync --locked before running this suite."
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "SYSTEMROOT", "TMPDIR", "TMP", "TEMP", "UV_CACHE_DIR")
        if key in os.environ
    }
    environment.update({
        "APP_ENV": "production",
        "DATABASE_URL": UNCONFIGURED_DATABASE_URL,
        "SUPABASE_URL": "http://127.0.0.1:1",
        "SUPABASE_ANON_KEY": "synthetic-production-test",
        "CORS_ORIGINS": "http://127.0.0.1:3000",
        "CURRENT_TERM": "202690",
        "SENTRY_DSN": "",
        "UV_PROJECT_ENVIRONMENT": str(runtime_copy / ".venv"),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_OFFLINE": "1",
        "UV_LINK_MODE": "copy",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    runtime = Runtime(runtime_copy, environment)
    runtime.run(
        [uv, "sync", "--locked", "--no-dev", "--python", sys.executable],
        environment={**environment, "UV_OFFLINE": "0"},
    )
    assert DEV_PACKAGES.isdisjoint(runtime.packages())
    return runtime


class Runtime:
    def __init__(self, project, environment):
        self.project = project
        self.environment = environment
        self.python = str(project / ".venv" / "bin" / "python")

    def run(self, command, *, environment=None):
        result = subprocess.run(
            command, cwd=self.project, env=environment or self.environment,
            text=True, capture_output=True, timeout=90, stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def packages(self):
        return json.loads(self.run([self.python, "-c", (
            "import importlib.metadata, json; "
            "print(json.dumps({d.metadata['Name'].lower(): d.version "
            "for d in importlib.metadata.distributions()}))"
        )]))


def test_all_runtime_modules_import_without_development_dependencies(production_runtime):
    runtime = production_runtime
    before = runtime.packages()
    runtime.run([runtime.python, "-c", (
        "import importlib, pkgutil; import main; "
        "[importlib.import_module(m.name) for m in pkgutil.walk_packages(['src'], 'src.')]; "
        "import scripts.migrate, scripts.verify_migrations, scripts.backfill_meetings"
    )])
    assert runtime.packages() == before


@pytest.mark.parametrize("command", SCRAPER_COMMANDS)
def test_scraper_startup_keeps_production_packages(production_runtime, command):
    runtime = production_runtime
    before = runtime.packages()
    lock_before = (runtime.project / "uv.lock").read_bytes()
    bootstrap = runtime.project / "synthetic-bootstrap"
    bootstrap.mkdir(exist_ok=True)
    # Execute the configured cron entrypoint, replacing only its external scrape
    # calls. Real imports/SQLAlchemy session creation run; no DB or HTTP I/O does.
    (bootstrap / "sitecustomize.py").write_text('''
from src.scrapers import banner, rmp
async def fake_banner(**kwargs):
    assert kwargs["term"] == "202690"
    print("SYNTHETIC_BANNER", flush=True)
async def fake_rmp(**kwargs):
    assert kwargs["term"] == "202690"
    print("SYNTHETIC_RMP", flush=True)
banner.run_banner_scrape = fake_banner
rmp.run_rmp_scrape = fake_rmp
''')
    environment = {**runtime.environment, "PYTHONPATH": os.pathsep.join((str(bootstrap), str(runtime.project)))}
    output = runtime.run(command, environment=environment)
    assert [line for line in output.splitlines() if line.startswith("SYNTHETIC_")] == [
        "SYNTHETIC_BANNER", "SYNTHETIC_RMP",
    ]
    assert runtime.packages() == before
    assert (runtime.project / "uv.lock").read_bytes() == lock_before


@pytest.mark.parametrize("command", API_COMMANDS)
def test_api_starts_with_production_packages(production_runtime, test_database_url, command):
    runtime = production_runtime
    before = runtime.packages()
    lock_before = (runtime.project / "uv.lock").read_bytes()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**runtime.environment, "DATABASE_URL": test_database_url, "PORT": str(port)}
    # Restrict this local subprocess to loopback; retain the deployed launcher.
    command = [part.replace("--host 0.0.0.0", "--host 127.0.0.1") for part in command]
    with (runtime.project / "api-startup.log").open("w+") as log:
        process = subprocess.Popen(
            command, cwd=runtime.project, env=environment, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True, stdin=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        health = json.load(response)
                    assert health["status"] == "ok"
                    assert health["db"] == "connected"
                    assert health["env"] == "production"
                    break
                except URLError:
                    time.sleep(0.05)
            else:
                log.seek(0)
                pytest.fail("Production API did not become healthy:\n" + log.read())
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    assert runtime.packages() == before
    assert (runtime.project / "uv.lock").read_bytes() == lock_before


def test_schema_verification_uses_only_production_packages(production_runtime, test_database_url):
    runtime = production_runtime
    before = runtime.packages()
    output = runtime.run(
        ["uv", "run", "--no-sync", "python", "-m", "scripts.verify_migrations"],
        environment={**runtime.environment, "DATABASE_URL": test_database_url},
    )
    assert "Runtime schema verified: public." in output
    assert runtime.packages() == before
