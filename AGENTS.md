# Repository Guidelines

## Project Structure & Module Organization
- `utu/` — core Python package: agents (`utu/agents`), tools (`utu/tools`), config loaders (`utu/config`), env, tracing, ui, utils.
- `configs/` — YAML configs for models, toolkits, and agents (e.g., `configs/agents/simple/base.yaml`).
- `tests/` — pytest suite mirroring package layout (e.g., `tests/tools/...`, `tests/agents/...`).
- `scripts/` — runnable utilities (CLI chat, eval, tool generation).
- `examples/` — ready-to-run samples; some require API keys.
- `docs/` + `mkdocs.yml` — documentation site.
- `frontend/`, `docker/`, `demo/`, `workspace/`, `logs/` — optional UI, container, demos, scratch, and runtime outputs.

## Build, Test, and Development Commands
- Sync env: `make sync` (installs dev extras with `uv`).
- Lint/format: `make format` or `make lint`; check only: `make format-check`.
- Tests: `uv run pytest -q`; example: `uv run pytest tests/test_config.py -q`.
- Docs: `make build-docs` (static), `make serve-docs` (live), `make deploy-docs` (gh-pages).
- Run CLI: `uv run python scripts/cli_chat.py --config_name simple/base` (workforce: `--config_name workforce/base`).
- Build UI wheel: `make build-ui` (requires `npm`).

## Coding Style & Naming Conventions
- Python 3.12; ruff line length 120; Google-style docstrings; mypy enabled with strict base.
- Names: modules/files `snake_case.py`; functions/vars `snake_case`; classes `CamelCase`.
- Imports: first‑party recognized as `utu` (ruff isort groups accordingly).
- Pre-commit: run `pre-commit install` to enable ruff format/check on commit.

## Testing Guidelines
- Framework: `pytest`; async supported via `pytest-asyncio`.
- Location: mirror source tree under `tests/`; name files `test_*.py` and functions `test_*`.
- Coverage: add tests for new features and bug fixes; include edge cases and config loading paths.

## Commit & Pull Request Guidelines
- Commits: use Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) or scoped prefixes (e.g., `tool_auto_gen:`).
- PRs: link to an issue; keep scope focused; pass lint/format/tests; include clear description, rationale, and screenshots/logs for UI or eval changes.

## Security & Configuration Tips
- Never commit secrets. Copy `.env.example` to `.env` and set required keys (LLM/tool APIs). `.env` is git-ignored.
- Prefer `uv run ...` to use the pinned virtual env and dependencies.
