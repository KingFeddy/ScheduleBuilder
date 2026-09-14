"""Opt-in checks using Docker's real context filtering and production images.

RUN_DOCKER_TESTS=1 enables these tests. Only Git-visible runtime sources and
synthetic artifacts enter the fixture; application .env files and the host venv
are never read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import warnings

import pytest


API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]
pytestmark = pytest.mark.docker
POISON = b"SYNTHETIC_DOCKER_EXCLUSION_SENTINEL\n"
ARTIFACTS = [
    ".env", ".env.local", ".env.production", ".env.example", "production.env",
    ".git/config", ".git/worktrees/local/HEAD", ".github/workflows/ci.yml",
    ".venv/pyvenv.cfg", ".venv/bin/python", ".venv/lib/python3.14/site-packages/local.py",
    "venv/lib/local.py", "env/lib/local.py", ".python-version", ".uv-cache/local.py",
    "__pycache__/main.cpython-314.pyc", ".pytest_cache/local.py", ".ruff_cache/local.py",
    ".coverage", "htmlcov/index.html", "coverage.xml", "tests/test_private.py",
    "node_modules/local/index.py", ".next/local.py", "playwright-report/index.html",
    "test-results/trace.zip", "playwright-browsers/local.py", "dist/local.py",
    "build/local.py", "local.egg-info/PKG-INFO", ".DS_Store", ".idea/workspace.xml",
    ".vscode/settings.json", ".claude/notes.md", ".codex/notes.md", "docs/notes.md",
    "dev-log.md", "mailmap.txt", "README.md", "railway.toml", "package.json",
    "student.pdf", "database.sql", "backup.zip", "debug.log", "credentials.json",
    "private.pem", "private.key", ".aws/credentials", ".ssh/id_rsa", ".npmrc",
]


def run(command, *, cwd, input=None, timeout=90):
    result = subprocess.run(
        command, cwd=cwd, input=input, text=True, capture_output=True, timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module", autouse=True)
def docker_available():
    if os.environ.get("RUN_DOCKER_TESTS") != "1":
        pytest.skip("Set RUN_DOCKER_TESTS=1 to run Docker build checks.")
    assert shutil.which("docker"), "Docker with Buildx must be installed."
    run(["docker", "info", "--format", "{{.ServerVersion}}"], cwd=REPO_ROOT)


@pytest.fixture
def poisoned_project(tmp_path):
    project = tmp_path / "project"
    api = project / "apps/api"
    api.mkdir(parents=True)
    # Avoid traversing ignored local files, while including uncommitted edits and
    # new runtime source files that have not yet been staged.
    sources = run([
        "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "apps/api",
    ], cwd=REPO_ROOT).split("\0")
    required = set()
    for filename in filter(None, sources):
        relative = Path(filename).relative_to("apps/api")
        if (
            str(relative) in {"main.py", "pyproject.toml", "uv.lock", "migrations/manifest.json"}
            or (relative.parts[0] in {"src", "scripts"} and relative.suffix == ".py")
            or (relative.parent == Path("migrations") and relative.suffix == ".sql")
        ):
            destination = api / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / filename, destination)
            required.add(relative.as_posix())
    # Include real control files, including any future per-Dockerfile override.
    # Missing ignore files are allowed here so the regression fails on leakage.
    for relative in (
        ".dockerignore", "apps/api/.dockerignore", "apps/api/Dockerfile",
        "apps/api/Dockerfile.scraper", "apps/api/Dockerfile.dockerignore",
        "apps/api/Dockerfile.scraper.dockerignore",
    ):
        source = REPO_ROOT / relative
        if source.exists():
            shutil.copy2(source, project / relative)
    # Plant artifacts at the root AND inside otherwise allowed runtime trees.
    # Nested .py files catch allow rules accidentally reopening env/cache dirs.
    for base in (project, api, api / "src", api / "src/nested", api / "scripts", api / "migrations"):
        for artifact in ARTIFACTS:
            destination = base / artifact
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(POISON)
    for filename in ("apps/web/src/page.tsx", "apps/other-service/main.py"):
        destination = project / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(POISON)
    return project, required


@pytest.mark.parametrize("context_root", ["api", "repo"])
@pytest.mark.parametrize("dockerfile", ["Dockerfile", "Dockerfile.scraper"])
def test_docker_context_contains_only_runtime_inputs(poisoned_project, tmp_path, context_root, dockerfile):
    # Export COPY's view of the context with no base pull or package downloads.
    # Retaining the real Dockerfile name also tests ignore-file precedence.
    project, required = poisoned_project
    api = project / "apps/api"
    (api / dockerfile).write_text("FROM scratch\nCOPY . /\n")
    context = api if context_root == "api" else project
    destination = tmp_path / "export"
    run([
        "docker", "buildx", "build", "--network=none", "--progress=plain",
        "--file", str(api / dockerfile), "--output", f"type=local,dest={destination}", str(context),
    ], cwd=project)
    prefix = "" if context_root == "api" else "apps/api/"
    actual = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
    assert actual == {prefix + filename for filename in required}
    assert all(POISON not in (destination / filename).read_bytes() for filename in actual)


@pytest.mark.docker_image
# Build/remove the larger image first to reduce peak disk use on cold runs.
@pytest.mark.parametrize("dockerfile", ["Dockerfile.scraper", "Dockerfile"])
def test_production_image_excludes_local_artifacts(poisoned_project, dockerfile):
    # SAFETY: secrets must never enter an image, and the host venv must not
    # overwrite the image-built interpreter or hide missing production imports.
    project, required = poisoned_project
    api = project / "apps/api"
    name = f"schedule-builder-docker-test-{uuid.uuid4().hex}"
    tag = f"{name}:local"
    try:
        run([
            "docker", "buildx", "build", "--load", "--platform=linux/amd64",
            "--progress=plain", "--tag", tag, "--file", str(api / dockerfile), str(api),
        ], cwd=project, timeout=1200)
        environment = {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+asyncpg://127.0.0.1:1/disabled_test_database",
            "SUPABASE_URL": "http://127.0.0.1:1", "SUPABASE_ANON_KEY": "synthetic-docker-test",
            "CORS_ORIGINS": "http://127.0.0.1:3000", "CURRENT_TERM": "202690", "SENTRY_DSN": "",
            "UV_OFFLINE": "1", "UV_CACHE_DIR": "/tmp/uv-cache", "PYTHONDONTWRITEBYTECODE": "1",
        }
        command = [
            "docker", "run", "--rm", "--name", name, "--platform=linux/amd64", "--interactive",
            "--network=none", "--read-only", "--tmpfs", "/tmp",
        ]
        for key, value in environment.items():
            command.extend(["--env", f"{key}={value}"])
        command.extend([tag, "uv", "run", "--no-sync", "python", "-"])
        inspection = '''
import base64
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import pkgutil
import sys

root = Path("/app")
actual = {
    path.relative_to(root).as_posix() for path in root.rglob("*")
    if path.is_file() and path.relative_to(root).parts[0] not in {".venv", "playwright-browsers"}
}
assert actual == set(EXPECTED), sorted(actual ^ set(EXPECTED))
assert all(SENTINEL not in (root / filename).read_bytes() for filename in actual)
assert SENTINEL not in (root / ".venv/pyvenv.cfg").read_bytes()
assert not (root / ".venv/lib/python3.14").exists()
assert not (root / "playwright-browsers/local.py").exists()
assert sys.platform == "linux" and sys.version_info[:2] == (3, 12)
assert sys.prefix == "/app/.venv"
packages = {dist.metadata["Name"].lower() for dist in importlib.metadata.distributions()}
assert {"pytest", "pytest-asyncio", "hypothesis"}.isdisjoint(packages)
assert "httpx" in packages
# Detect damaged cached wheels explicitly, before a truncated driver can crash.
distribution = importlib.metadata.distribution("playwright")
driver = next(path for path in distribution.files if str(path) == "playwright/driver/node")
driver_path = distribution.locate_file(driver)
assert driver_path.stat().st_size == driver.size, "Playwright driver size differs from wheel RECORD"
with driver_path.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").digest()
assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == driver.hash.value
import main
for module in pkgutil.walk_packages(["src"], "src."):
    importlib.import_module(module.name)
import scripts.migrate, scripts.verify_migrations, scripts.backfill_meetings
if SCRAPER:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        page = browser.new_page()
        page.set_content("<title>Synthetic Docker check</title>")
        assert page.title() == "Synthetic Docker check"
        browser.close()
print(json.dumps({"runtime_files": len(actual), "python": sys.version.split()[0], "scraper": SCRAPER}))
'''
        output = run(command, cwd=project, input=(
            f"EXPECTED = {sorted(required)!r}\nSENTINEL = {POISON!r}\n"
            f"SCRAPER = {dockerfile == 'Dockerfile.scraper'!r}\n" + inspection
        ))
        result = json.loads(output)
        assert result["runtime_files"] == len(required)
        assert result["scraper"] == (dockerfile == "Dockerfile.scraper")
    finally:
        # Only the unique container/image created by this test can be removed.
        # Docker's reusable build cache is left under the builder's own policy.
        for command, missing in (
            (["docker", "rm", "--force", name], "No such container"),
            (["docker", "image", "rm", tag], "No such image"),
        ):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                if result.returncode and missing not in result.stderr:
                    warnings.warn(f"Docker cleanup failed for {name}: {result.stderr}", stacklevel=2)
            except subprocess.TimeoutExpired:
                warnings.warn(f"Docker cleanup timed out for {name}.", stacklevel=2)
