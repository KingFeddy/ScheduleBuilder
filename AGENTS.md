# Repository workflow

- Work directly on `main` unless the user explicitly requests another branch.
  Do not create branches or worktrees for routine changes.
- Leave changes uncommitted for the user to review and commit. Do not stage,
  commit, push, or merge unless the user explicitly requests that action.
- Keep project descriptions focused on its functionality. Do not add assistant
  attribution, tool credits, or automated co-author trailers to documentation,
  the website, release notes, or commit messages.
- Run tests only when necessary for the change. Prefer focused existing checks;
  use broader suites for changes that affect shared behavior or the test suite.
- Add a regression test when it protects a meaningful failure mode. Reuse
  existing coverage and representative cases instead of adding tests for every
  equivalent input or multiplying parameter combinations.
- Preserve coverage for core scheduling and planning workflows, persistence,
  scraper data loss, and database safety. Never use the application database
  for tests; use the guarded disposable database fixtures.

These workflow instructions supersede older branch and tests-first guidance in
project documents. Follow explicit user instructions when they differ.
