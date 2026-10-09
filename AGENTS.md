# ADK Starter — Agent & Contributor Engineering Guidelines

Welcome to the **ADK Starter** repository. This document is the engineering contract for human contributors and AI coding agents. The automated PR reviewer (`.github/prompts/review-instructions.md`) cites these section numbers, so **extend sections in place and never renumber them**.

---

## 1. Primary Authority & Formatting

- **Baseline Standard**: Adhere to the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
- **Line Length**: 100 characters maximum across Ruff, Mypy, and documentation.
- **Python Version**: Python 3.12+ (`>=3.12,<3.14` in `pyproject.toml`).
- **Tooling Stack**: Astral `uv` for package management, `ruff` for linting/formatting, `mypy` for strict type checking, and `pytest` for testing. Use `make` targets (or `uv run ...`); never run `pip install` directly.
- **Lockfile Hygiene**: `uv.lock` must resolve exclusively from public PyPI (`make lock`).

---

## 2. Python Strict Typing & Language Invariants

The codebase enforces `mypy --strict` with `disallow_any_explicit = true`.

### 2.1 Header-Only Imports
All imports **MUST** reside at the top of the file in three groups: (1) standard library, (2) third-party (`google.adk`, `google.genai`, `pydantic`), and (3) first-party (`app.*`). Relative imports (Ruff `TID252`) and inline imports inside functions or branches are forbidden.

### 2.2 Zero `Any` Policy
`typing.Any` is prohibited across `app/`, `scripts/`, and `tests/`. Use generics (`TypeVar` / PEP 695), `typing.Protocol` (including for callables instead of `Callable[..., T]`), explicit union types, or `object` narrowed with `isinstance(...)`.

### 2.3 Defensive Against `None` (Avoid Null Sprawl)
Prefer immutable empty collections (e.g., `Sequence[T] = ()`) over `None`. Where `None` is semantically required, unwrap it immediately at the boundary with an early guard clause.

### 2.4 Mandatory `StrEnum` for Discrete Entities
Never represent discrete categories (agent names, tool statuses, severity levels) as raw `str`. Inherit from `enum.StrEnum` (`app/models/enums.py`) and coerce external strings during Pydantic validation.

### 2.5 Keyword-Only Arguments (`*`)
Functions or methods accepting **3 or more parameters**, or any boolean/configuration flags, MUST enforce keyword-only arguments using `*`.

### 2.6 Zero Mutable Default Arguments
Mutable default arguments (`list = []`, `dict = {}`) are forbidden. Use immutable default tuples (`Sequence[str] = ()`) or `None`.

### 2.7 Strict `# type: ignore` Discipline
`# type: ignore` is allowed **ONLY** when third-party stubs are missing or inaccurate, and must include a specific error code and reason (e.g., `# type: ignore[import-untyped]  # no PEP 561 stubs`). Prefer scoped `[[tool.mypy.overrides]]` entries in `pyproject.toml`.

### 2.8 Declarative Pydantic Models & Composable Validation
- **Frozen Models**: Domain models must use `model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)`. Pass frozen Pydantic models rather than raw `dict[str, object]` across domain boundaries.
- **Declarative Constraints**: Prefer `Field(ge=..., le=..., min_length=..., max_length=...)`, `StrEnum`, and `typing.Annotated[T, Field(...), AfterValidator(...)]` over imperative `@field_validator` methods.
- **Canonical Field Names**: Use one canonical name per field; use `AliasChoices` only when migrating external payloads.

### 2.9 Idiomatic Simplicity & Readability
Favor standard Python idioms and eliminate unnecessary wrapper layers, premature caching, or redundant abstractions.

### 2.10 Clean Code & Robustness Invariants
- **Logging Only**: Prohibit `print()` in `app/`; use `logging.getLogger(__name__)`. (CLI scripts in `scripts/` may write to stdout/stderr.)
- **No Silent Exception Swallowing**: Bare `except:` and `except Exception: pass` are prohibited. Catch specific exceptions, log context, and raise domain-specific errors.
- **Pathlib Exclusively**: Use `pathlib.Path` rather than `os.path`.
- **Timezone-Aware Datetimes**: Use `datetime.datetime.now(datetime.UTC)`; naive datetimes are forbidden.

### 2.11 Anti-Redundancy, Single Source of Truth & Clean Code Contract
1. **Single Source of Truth for Constants**: Define each constant once with `typing.Final[...]` (model IDs in `app/agents/constants.py`, state keys in `app/agents/state.py`, app identity in `app/constants.py`).
2. **Package-Level `__all__` Discipline**: Define `__all__` only in package `__init__.py` files, never in internal submodules.
3. **Trust Validated Models**: Do not repeat checks already enforced by Pydantic or add dict-like methods (`__getitem__`, `keys()`) to domain models.

### 2.12 Google-Style Docstrings
All public modules, classes, and functions must have concise Google-style docstrings with `Args:`, `Returns:`, and `Raises:` sections where applicable.

---

## 3. Implementation Logic & Domain Soundness

### 3.1 Algorithmic & Data Correctness
- **Deterministic Transformations**: Identical validated inputs must produce identical outputs without depending on unordered iteration or wall-clock time.
- **Numeric Rigor**: Use `decimal.Decimal` for exact quantities, never compare floats with `==`, and distinguish `None` from `0`.
- **Query, Path & Identifier Hygiene**: Never interpolate untrusted or LLM-generated strings into SQL, shell commands, file paths, or object names.
- **LLM Output Is Untrusted Input**: Validate tool arguments and model outputs with Pydantic before acting on them.
- **Idempotent Side Effects**: Side-effecting operations (writes, exports, external calls) must be idempotent or explicitly guarded.

### 3.2 Project-Specific Domain Standards
<!-- CUSTOMIZE: replace this placeholder with your project's domain rules after `make init`. -->
> **Template placeholder — not yet customized.** Replace this block with the domain rules of your agent: business invariants, approval or human-in-the-loop thresholds, reconciliation and validation requirements, output-format fidelity, data residency and so on. Write each rule so that a reviewer can check a diff against it. Until this placeholder is replaced, the AI reviewer skips §3.2.

### 3.3 Boundary Conditions & Defensive Invariants
- **Clamped Ranges**: Validate domain boundaries declaratively (`Field(min_length=1, max_length=...)`, bounded numeric ranges).
- **Fail-Closed Domain Exceptions**: Raise a subclass of `AgentDomainError` (`app/models/exceptions.py`) when inputs violate domain rules rather than silently returning partial data. `DomainErrorGuardPlugin` converts these into structured tool error payloads.

---

## 4. Latest Documentation & Package Modernity

### 4.1 Grounding on Verified Latest Documentation
Verify API contracts against official documentation (ADK: `https://adk.dev/`) and the installed environment (`uv run python -c "import google.adk; print(google.adk.__version__)"`). Speculative APIs are prohibited.

### 4.2 Zero Deprecated or Legacy Patterns
- **Google ADK (v2.8+)**: Use `App(name="app", ...)`, `LlmAgent`, workflow agents (`SequentialAgent`, `ParallelAgent`, `LoopAgent`), `ToolContext` (`from google.adk.tools.tool_context`), and `BasePlugin`.
- **Pydantic (v2.13+)**: Use `ConfigDict(frozen=True)`, `@field_validator`, `@model_validator`, and `.model_dump()`. Never use Pydantic V1 `class Config:`, `@validator`, or `.dict()`.
- **Python 3.12+**: Use built-in generics (`list[T]`, `dict[K, V]`), `type` statements, `T | None` unions, and `enum.StrEnum`.
- **Google GenAI SDK**: Use `google-genai`; never import deprecated `google.generativeai` or `vertexai.generative_models`.

---

## 5. Google ADK Code Best Practices

### 5.1 App Invariant
`app/agent.py` is the composition root declaring `app = App(name=APP_NAME, root_agent=root_agent, plugins=[...])`, and `app/__init__.py` re-exports `app` and `root_agent`. Keep `APP_NAME = "app"` (`app/constants.py`) so `agents-cli`, `adk web`, `/a2a/app`, and the Agent Runtime adapter stay aligned.

### 5.2 Tool Function Rules
- **Complete Type Hints & Docstrings**: Provide full type annotations and Google-style docstrings (`Args:`, `Returns:`, `Raises:`). The docstring forms the model-visible tool schema.
- **Injected Context Hygiene**: Declare framework-injected parameters (e.g., `tool_context: ToolContext`) keyword-only and omit them from the docstring.
- **Return & Error Contracts**: Return JSON-serializable `dict[str, object]` payloads carrying `status` (`ToolStatus`), and raise `AgentDomainError` subclasses for recoverable domain errors.

### 5.3 State Namespaces (`session.state`)
Use canonical `Final[str]` keys from `app/agents/state.py` (never string literals) with standard prefixes: unprefixed for session state, `"user:"` for persistent user state, `"app:"` for application state, and `"temp:"` for turn-scoped scratch state.

### 5.4 Deterministic Orchestration
Use workflow agents (`SequentialAgent`, `ParallelAgent`, `LoopAgent`) for fixed pipelines and `sub_agents` delegation for open-ended routing, gating irreversible actions behind human confirmation. Construct every agent via a factory `create_<name>_agent(*, model: str = DEFAULT_MODEL) -> LlmAgent`.

### 5.5 Zero Module-Level Mutable State
Module-level mutable state is forbidden; store runtime state in `session.state` or pass it via typed arguments.

### 5.6 Async/Sync Hygiene
Offload blocking I/O or CPU-bound work inside `async def` functions with `asyncio.to_thread`.

### 5.7 No Hardcoded Session or User ID Defaults
Entrypoints and runner helpers must never hardcode fallback strings such as `"default_user"` or `"default_session"`.

### 5.8 Folder Architecture & Layering
Dependencies under `app/` point strictly downwards (`agent.py` → `agents` → `tools` → `services` → `models`) and are enforced by `tests/unit/test_architecture.py`:
- **`app/services/` never imports `google.adk`**; `app/models/` imports nothing from other layers.
- **`app/tools/` are thin adapters**: validate arguments, call a service, update `session.state` via keys from `app/agents/state.py`, and return a dict. Tools never import agent factories.
- **Prompts live in each agent's `prompts.py`**; agent factories never embed instruction or description literals.
- **Model IDs live in `app/agents/constants.py`**; nothing else hardcodes a model name.
- **The serving layer (`app.agent`, `app.fast_api_app`, `app.app_utils`) is a leaf**: nothing below `agent.py` imports it.

---

## 6. Mandatory Unit Tests for Application Code

### 6.1 Application Code Scope Boundary
Changes to `app/` require corresponding unit tests in `tests/unit/`. Pull requests touching only documentation (`*.md`), workflows (`.github/`), or Terraform (`terraform/`) do not require new unit tests.

### 6.2 Test Directory Structure Mirroring `app/`
Tests under `tests/unit/` mirror the source tree (e.g., `app/models/schema.py` → `tests/unit/models/test_schema.py`, `scripts/ci/review_report.py` → `tests/unit/scripts/ci/test_review_report.py`).

### 6.3 Mandatory Table-Driven Testing
Tests covering multiple inputs or boundary conditions must use `@pytest.mark.parametrize` with descriptive `pytest.param(..., id="...")` IDs.

### 6.4 100% Offline Isolation
Unit tests run completely offline (`tests/conftest.py` blocks network access and mocks credentials and GenAI clients). Test agent flows with `InMemoryRunner` and canned responses.

### 6.5 Deterministic Assertions
Assert on structured types, Pydantic models, enum statuses, state mutations, and tool calls—never on non-deterministic freeform LLM prose.

### 6.6 Local Verification Suite
Run `make check` (`ruff check`, `ruff format --check`, `shellcheck`, `actionlint`, `mypy app tests scripts --strict`, and `pytest --cov` with an 80% coverage floor) before opening a pull request.

---

## 7. Repository Security & Hygiene

### 7.1 Zero Static Keys Policy
Never generate, store, or commit `.json` service account keys, API tokens, or static secrets. Authenticate to Google Cloud exclusively via Workload Identity Federation (WIF) and Application Default Credentials (ADC).

### 7.2 Zero Private Identifier Leakage
Do not reference private issue tracker IDs, internal URLs, or confidential customer identifiers in commit messages, pull requests, comments, docstrings, or workflows.

### 7.3 Infrastructure as Code (Terraform) Standards
- **Portability & Safe Parameterization**: Zero hardcoded GCP project IDs, project numbers, or bucket names in defaults or resources. Derive names from `var.resource_prefix` and `var.project_id`, and keep `enable_cicd = false` as the default in `variables.tf`.
- **Non-Authoritative IAM**: Always use `google_*_iam_member` rather than authoritative `google_*_iam_binding` or `google_*_iam_policy`.
- **Least Privilege & Safe State**: Prefer granular IAM roles; enforce versioning, uniform bucket-level access, and public access prevention on state buckets; record resource renames with `moved {}` blocks in `migrations.tf`; never commit `*.tfvars` or `terraform.tfstate*`.

### 7.4 CI/CD Workflow Hygiene
All workflows under `.github/workflows/` are checked by `tests/unit/test_workflows.py`:
- Pin every action to a 40-character commit SHA with a `# vX.Y.Z` comment.
- Declare least-privilege `permissions:` (`contents: read` by default; `id-token: write` only for GCP-authenticated jobs).
- Read cloud identifiers from `vars.*` (never hardcoded) and gate GCP jobs on `vars.GCP_WIF_PROVIDER != ''` while skipping fork and `dependabot[bot]` PRs.
- Set `persist-credentials: false` on `actions/checkout`, pass untrusted values via `env:` instead of `${{ }}` inside `run:`, set `timeout-minutes` on every job, and delegate deployments to `scripts/deploy.sh`.

---

## 8. PR & Git Workflow Guidelines

### 8.1 Pragmatic Atomic PRs
Keep pull requests focused on a single responsibility (target 100–250 lines of code).

### 8.2 Commit Message Format
Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) (`<type>(<optional scope>): <imperative summary under 72 characters>` with types `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`, `perf`).

### 8.3 Automated AI Code Review & Style Standards
Same-repository pull requests are reviewed by Claude Code Action (`anthropics/claude-code-action`) on Vertex AI according to `AGENTS.md` and `.github/prompts/review-instructions.md`. The reviewer outputs `review.json`, which `scripts/ci/publish_review.py` verifies and publishes as advisory feedback.

### 8.4 Zero Autonomous Merging Policy (Mandatory Human Review Invariant)
- **AI agents MUST NEVER automatically or autonomously merge pull requests (e.g., via `gh pr merge`) under any circumstances.**
- Every pull request requires explicit review and approval by a human maintainer before merging.
