# Wavefunction engine (backend)

Python 3.12, FastAPI and LangGraph. Read the root [AGENTS.md](../AGENTS.md) first; [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) follows one task through the system.

## Where things are (`src/wavefunction/`)

| Area | Modules | Tests (`tests/`) |
|---|---|---|
| HTTP API, app startup, services | `main.py` (runs, models and providers, connectors and catalog, skills library, folder grants, notifications, ChatGPT sign-in), `coding_workspace.py` (`/api/coding/*`: chats, projects, messages, files, terminals, access), `__main__.py` (frozen entry point) | `test_api.py`, `test_optional_startup.py`, `test_chats.py` |
| Chats, projects and storage | `coding_workspace.py`, `repository.py`, `records.py`, `database.py`, `routines.py`, `routine_timing.py`, `coding_analytics.py`, `notifications.py` | `test_chats.py`, `test_projects.py`, `test_routines.py`, `test_notifications.py`, `test_models.py`, `test_change_records.py` |
| Running a task | `runtime.py`, `agent_factory.py`, `execution_policy.py`, `steering.py`, `spec_v2.py`, `toolkit.py`, `tool_discovery.py`, `local_coding.py` | `test_execution_policy.py`, `test_run_record.py`, `test_run_settlement.py`, `test_context_compaction.py`, `test_no_progress.py`, `test_truncation_recovery.py` |
| Helpers (the agent's own sub-agents) | `coding_agents.py` | `test_helper_orchestration.py`, `test_run_settlement.py` |
| Local tools and the sandbox | `hands.py`, `fileops.py`, `sandbox.py`, `irreversible.py`, `coding_access.py`, `folder_setup.py`, `terminals.py`, `terminal_tools.py`, `web.py` | `test_coding_tools.py`, `test_terminals.py`, `test_permissions.py`, `test_file_editing.py`, `test_environment_context.py`, `test_repository_discovery.py`, `test_sandbox.py` (destructive) |
| Git, worktrees and history | `coding_delivery.py`, `coding_git_ops.py`, `coding_checkpoints.py`, `coding_snapshots.py`, `coding_recovery.py`, `coding_cleanup.py`, `change_review.py`, `github_cli.py`, `github_proxy.py` | `test_coding_delivery.py`, `test_worktree_cleanup.py`, `test_helper_git.py`, `test_github_setup.py`, `test_coding_checkpoints.py` (destructive) |
| Models and providers | `models.py`, `provider_chat.py`, `gateway_chat.py`, `codex.py`, `anthropic_provider.py`, `gemini_provider.py`, `ollama_provider.py`, `openrouter.py`, `vercel_gateway.py`, `reasoning.py`, `model_registry.py` | `test_codex*.py`, `test_*_provider.py`, `test_openrouter.py`, `test_vercel_gateway.py`, `test_model_registry.py` |
| Connectors (MCP) | `connectors.py`, `connector_catalog.py`, `connector_oauth.py`, `connector_setup.py`, `catalog_setup.py`, `catalog_connections.py`, `local_integrations.py`, `integration_setup.py`, `mail_*.py`, `microsoft_*.py`, `n8n_mcp.py` | `test_connectors.py` |
| Skills | `skills.py`, `skill_discovery.py`, `skills/` (built in), `coding_skills/` | `test_skill_versions.py`, `test_slash_skills.py`, `test_skill_catalog.py` |
| Files, documents, images | `files.py`, `documents.py`, `document_images.py`, `local_images.py`, `file_previews.py` | `test_document_uploads.py` |
| Computer use (Docker desktop) | `computer.py`, `computer_control.py`, `computer_routes.py`, `desktop_files.py`, `setup_rows.py`, `sandbox_image/` | `test_computer.py`, `test_computer_takeover.py` |
| Settings and secrets | `config.py`, `secrets.py`, `schemas.py` | `test_secrets.py` |
| API response shapes | `responses.py` (the `response_model` of each route) | `conftest.py` checks every response against its model |
| What the model reads | assembled by `agent_factory.py`, `local_coding.py`, `toolkit.py`; committed in `tests/fixtures/prompt_surface/default_chat.md` | `test_prompt_surface.py` |

The tests drive the HTTP API, so find a behavior's tests by its route or name, not by imports: `rg -l "/fork" tests/`.

## Testing

- Run pytest from this folder: `uv run --no-sync pytest tests/test_x.py -k name`. The whole suite: `npm run test:backend` from the repository root.
- `tests/conftest.py` gives each run a temporary data folder and signs `gh` out. Its `coding` fixture serves the API over isolated services; `ScriptedModel`, `tool_call_message` and `script_gateway` drive real tool loops without a model. The plain helpers `create`, `git` and `repository` are in `tests/support.py`. No test module imports another.
- Markers: `sandbox` (needs macOS Seatbelt; skipped elsewhere), `destructive` (runs real deletions; CI only, never locally), `live` (real external services; opt-in with `-m live`).
- The budget (count and time) is in [docs/TESTING.md](../docs/TESTING.md) and enforced by `conftest.py`.

## Rules here

- Find callers with `rg` before changing shared runtime code.
- Keep each run's captured spec and guidance, checkpoints and file versions intact.
- Real accounts, the Keychain, credentials and user repositories stay out of ordinary tests.
- Live connector checks (`npm run test:connectors:live`) are opt-in and never use real account mutations as fixtures.
