"""The JSON each API route returns, as `response_model`s, so the OpenAPI schema and the
TypeScript generated from it (`npm run generate:api`) describe it.

Routes build plain dicts, and some of their keys are present only sometimes. These are
TypedDicts rather than BaseModels for that reason: a key a route leaves out stays out (a
`NotRequired` field), where a BaseModel would add it with its default. Keys a type does not
declare are dropped from the response, so a new key goes here too; the backend suite fails
when a route returns one that is missing (see `tests/conftest.py`).

Saved JSON whose keys grew over time (a run's input, output and usage, setup cards) is
`extra="allow"`: its declared keys are typed and the rest pass through unchanged.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import ConfigDict, Field, with_config

from .reasoning import ReasoningEffort
from .spec_common import Provider

# Saved and reported numbers keep their JSON form: an int is not turned into a float.
Num = int | float
RunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "waiting_connector",
    "waiting_computer",
    "completed",
    "failed",
    "cancelled",
]
Access = Literal["sandboxed", "network"]
ProjectIcon = Literal[
    "folder",
    "code",
    "terminal",
    "globe",
    "rocket",
    "app",
    "box",
    "layers",
    "database",
    "cloud",
    "server",
    "shield",
    "lock",
    "key",
    "zap",
    "lightbulb",
    "wrench",
    "gear",
    "bug",
    "flask",
    "test-tube",
    "git-branch",
    "cpu",
    "chip",
    "book",
    "file",
    "palette",
    "brush",
    "camera",
    "music",
    "gamepad",
    "heart",
]
OPEN = ConfigDict(extra="allow")


# Models


class ModelChoice(TypedDict):
    """A saved ModelSpec. A chat's model is saved as its settings sent it, so the fields
    after `model` can be missing, as `reasoning_effort` is from the built-in default."""

    provider: Provider
    model: str
    base_url: NotRequired[str | None]
    temperature: NotRequired[Num]
    max_tokens: NotRequired[int | None]
    reasoning_effort: NotRequired[ReasoningEffort | None]


class GatewayModel(TypedDict):
    id: str
    name: str
    description: str
    context_length: int
    input_modalities: list[str]
    output_modalities: list[str]
    supported_parameters: list[str]
    pricing: dict[str, str]
    reasoning_efforts: list[str] | None
    release_date: str | None


class GatewayModels(TypedDict):
    models: list[GatewayModel]


class AnthropicModel(GatewayModel):
    max_output_tokens: int | None
    adaptive_thinking: bool


class AnthropicModels(TypedDict):
    models: list[AnthropicModel]


class GeminiModel(GatewayModel):
    max_output_tokens: int | None


class GeminiModels(TypedDict):
    models: list[GeminiModel]


class CodexStatus(TypedDict):
    connected: bool
    account_id: str
    email: str
    expires_at: Num
    signing_in: bool
    error: str


class CodexModel(TypedDict):
    id: str
    name: str
    description: str
    # The built-in fallback list has none.
    reasoning_efforts: NotRequired[list[str] | None]
    release_date: NotRequired[str | None]


class CodexLogin(TypedDict):
    url: str
    status: CodexStatus


class OllamaModel(TypedDict):
    id: str
    name: str
    capabilities: list[str]
    context_length: int
    # Discovery always sets these; tests that stub discovery leave them out.
    size: NotRequired[Num]
    description: NotRequired[str]


class OllamaConfig(TypedDict):
    base_url: str
    num_ctx: int
    models: list[OllamaModel]
    connected: bool


class SavedSecrets(TypedDict):
    openai_api_key: bool
    openrouter_api_key: bool
    vercel_api_key: bool
    anthropic_api_key: bool
    gemini_api_key: bool
    tavily_api_key: bool


class Connections(TypedDict):
    model: ModelChoice
    enabled_models: list[str]
    coding_models: list[ModelChoice]
    secrets: SavedSecrets
    codex: CodexStatus
    codex_models: list[CodexModel]
    ollama: OllamaConfig | None
    require_zdr: bool


class DefaultModel(TypedDict):
    model: ModelChoice


class EnabledModels(TypedDict):
    models: list[str]
    model: ModelChoice


class ZeroDataRetention(TypedDict):
    require_zdr: bool


class ReasoningOptions(TypedDict):
    # None when the model's controls are unknown, [] when it has none.
    efforts: list[ReasoningEffort] | None


# Skills


class SkillRecord(TypedDict):
    name: str
    description: str
    body: str
    version: int


class DiscoveredSkill(TypedDict):
    name: str
    description: str
    body: str
    source: Literal["repository", "global", "library", "builtin"]
    path: str | None
    active: bool
    version: int | None
    digest: str | None
    created_at: str | None
    modified_at: str | None


class SkillInventory(TypedDict):
    skills: list[DiscoveredSkill]
    warnings: list[str]


class CatalogSkill(DiscoveredSkill):
    scope: Literal["builtin", "global", "project"]
    editable: bool
    project_id: NotRequired[str]
    project_name: NotRequired[str]
    overrides: NotRequired[bool]
    overridden_by: NotRequired[Literal["builtin", "global", "project"]]
    # A bundled skill offered only with its connector.
    condition: NotRequired[str]


class SkillCatalog(TypedDict):
    skills: list[CatalogSkill]
    warnings: list[str]


class SkillFile(TypedDict):
    name: str
    path: str


# Connectors and local integrations


class SecretState(TypedDict):
    name: str
    set: bool


class ToolName(TypedDict):
    name: str


class ToolSummary(TypedDict):
    name: str
    description: str


class McpServerFields(TypedDict):
    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str
    url: str
    headers: dict[str, str]
    enabled: bool
    auth: Literal["none", "oauth"]


class Connector(McpServerFields):
    """A connector in the list, with its saved keys and what it last offered."""

    secrets: list[SecretState]
    connected: bool
    checking: bool
    error: str
    tools: list[ToolSummary]


class SavedConnector(McpServerFields):
    """A connector just added or changed, and whether it answered."""

    connected: bool
    error: str
    tools: list[ToolName]


class ConnectorCheck(TypedDict):
    connected: bool
    error: str
    tools: list[ToolName]


class ConnectorProbe(TypedDict):
    url: str
    transport: Literal["http", "sse"]
    reachable: bool
    auth: Literal["none", "oauth", "token", "unreachable"]
    detail: str
    resource_metadata: NotRequired[str]
    # The address as typed, and whether the one above had to be looked for instead.
    typed_url: str
    guessed: bool
    name: str


class CatalogEntry(TypedDict):
    id: str
    title: str
    description: str
    url: str
    auth: Literal["oauth", "none", "api_key"]
    transport: Literal["http", "stdio"]
    docs: str
    notes: str
    local: bool
    setup: NotRequired[Literal["url", "registered_oauth", "api_key"]]
    oauth_default_auth_method: NotRequired[str]
    oauth_auth_methods: NotRequired[list[str]]
    oauth_default_scope: NotRequired[str]
    oauth_callback: NotRequired[str]


class CatalogConnecting(TypedDict):
    name: str
    auth: str


class CatalogConnectionState(TypedDict):
    state: Literal["connecting", "connected", "failed", "cancelled"]
    error: str


class CatalogSetup(TypedDict):
    url: str
    redirect_uri: str
    client_id: str
    scope: str
    auth_method: str
    secret_saved: Literal["true", "false"]


class Cancelled(TypedDict):
    cancelled: bool


class Installed(TypedDict):
    installed: bool


class EmailAccount(TypedDict):
    account: str


class SignInRunning(TypedDict):
    running: bool


class GoogleSignInState(TypedDict):
    running: bool
    error: str


class DownloadedCredentials(TypedDict):
    name: str
    path: str
    project_id: str


class FoundCredentials(TypedDict):
    found: DownloadedCredentials | None


class MicrosoftAttempt(TypedDict):
    id: str


class MicrosoftSignInState(TypedDict):
    running: bool
    account: str
    error: str
    device_code: NotRequired[str]


class GitHubSignInState(TypedDict):
    running: bool
    code: str
    account: str
    error: str


class IntegrationAccount(TypedDict):
    id: str
    label: str
    # Google
    services: NotRequired[list[str]]
    scopes: NotRequired[list[str]]
    # Microsoft
    email: NotRequired[str]
    mode: NotRequired[Literal["mail", "workspace"]]
    account_type: NotRequired[Literal["personal", "work"]]
    allow_write: NotRequired[bool]


class LocalIntegrationStatus(TypedDict):
    installed: bool
    version: str
    accounts: list[IntegrationAccount]
    error: str


# Runs


@with_config(OPEN)
class ReferenceFile(TypedDict):
    id: str
    name: str
    media_type: NotRequired[str]
    size_bytes: NotRequired[int]


class CapturedFile(TypedDict):
    path: str
    start_line: int
    end_line: int | None
    version: str
    folder: NotRequired[str]


class CodingContext(TypedDict):
    files: list[CapturedFile]
    skills: list[str]
    command: str | None


@with_config(OPEN)
class RoutineReport(TypedDict):
    thread_id: str
    title: str
    status: RunStatus


@with_config(OPEN)
class RunInput(TypedDict):
    text: str
    display_text: NotRequired[str]
    reference_files: NotRequired[list[ReferenceFile]]
    initial_reference_files: NotRequired[list[ReferenceFile]]
    coding_context: NotRequired[CodingContext]
    # Set when a routine sent this message to report a finished run.
    routine_report: NotRequired[RoutineReport]


@with_config(OPEN)
class RunOutput(TypedDict):
    text: str


@with_config(OPEN)
class InputTokenDetails(TypedDict):
    cache_read: NotRequired[Num]
    cache_creation: NotRequired[Num]


@with_config(OPEN)
class OutputTokenDetails(TypedDict):
    reasoning: NotRequired[Num]


@with_config(OPEN)
class Usage(TypedDict):
    """Provider-reported totals. Cache and reasoning counts are subsets, not extra tokens."""

    input_tokens: NotRequired[Num]
    output_tokens: NotRequired[Num]
    total_tokens: NotRequired[Num]
    input_token_details: NotRequired[InputTokenDetails]
    output_token_details: NotRequired[OutputTokenDetails]
    cost: NotRequired[Num]


class Run(TypedDict):
    id: str
    project_id: str
    conversation_id: str | None
    status: RunStatus
    input: RunInput
    output: RunOutput | None
    error: str | None
    started_at: str
    completed_at: str | None
    duration_ms: Num | None
    usage: Usage
    # "user", "compact" for a Compact now run, "fork" for a copied one.
    kind: str
    parent_run_id: str | None


class RunEvent(TypedDict):
    id: str
    run_id: str
    sequence: int
    event_type: str
    node_id: str | None
    payload: dict[str, Any]
    created_at: str


class RunWithEvents(Run):
    events: list[RunEvent]


class AnsweredRun(Run):
    # The configuration the task captured. Answering questions returns it; other run
    # responses leave it out.
    spec: dict[str, Any] | None


class Checkpoint(TypedDict):
    id: str
    run_id: str
    kind: Literal["before_user", "after_assistant", "before_steering"]
    event_id: str | None
    available: bool
    unavailable_reason: str | None


class ChatRun(Run):
    history_order: int
    events: list[RunEvent]
    checkpoints: list[Checkpoint]
    # What the helpers this task started spent, follow-ups included.
    helper_usage: NotRequired[Usage]


@with_config(OPEN)
class SetupRow(TypedDict):
    """A row of a paused task's setup card: a connector, folder, GitHub or computer use."""

    id: str
    title: str
    catalog_id: str | None
    auth: str
    state: Literal["ready", "setup", "sign_in", "connecting", "error", "disabled"]
    error: str
    tool_count: int
    signing_in: bool
    local_kind: NotRequired[Literal["computer", "folder", "github", "himalaya", "microsoft", "gog"]]
    # Computer use
    build_log: NotRequired[list[str]]
    docker_startable: NotRequired[bool]
    docker_action: NotRequired[Literal["start", "restart"] | None]
    image_update: NotRequired[bool]
    # A folder card: the folder asked for, "" for any the user picks, and whether it may change files.
    path: NotRequired[str]
    writable: NotRequired[bool]
    # Accounts
    account: NotRequired[str]
    services: NotRequired[list[str]]
    mode: NotRequired[str]
    allow_write: NotRequired[bool]


class RunSetup(TypedDict):
    status: RunStatus
    project_id: str
    connectors: list[SetupRow]


class GitHubAccount(TypedDict):
    account: str


class FolderChosen(TypedDict):
    chosen: bool


# Folder grants and the computer


class GrantRecord(TypedDict):
    path: str
    writable: bool


class Grants(TypedDict):
    grants: list[GrantRecord]


class GrantState(Grants):
    # False when the boundary could not be proved, e.g. a macOS release broke the profile.
    sandbox_ok: bool
    sandbox_problem: str | None


class ComputerStatus(TypedDict):
    docker_available: bool
    docker_problem: str | None
    image_ready: bool
    image_update_available: bool
    building: bool
    build_log: list[str]
    docker_desktop_available: bool
    docker_action: Literal["start", "restart"] | None


class ComputerBuild(TypedDict):
    started: bool
    building: bool


class ControlStatus(TypedDict):
    available: bool
    state: Literal["agent", "pausing", "user"]
    generation: int
    waiting: bool


class Accepted(TypedDict):
    ok: bool


# The desktop shell


class Activity(TypedDict):
    active: bool


class Health(TypedDict):
    status: str
    service: str
    version: str
    data_dir: str


class Notice(TypedDict):
    thread_id: str
    title: str
    body: str


class PendingNotifications(TypedDict):
    cursor: str
    sound: bool
    items: list[Notice]


# Coding workspace


class Project(TypedDict):
    id: str
    name: str
    folder: str | None
    created_at: str
    icon: ProjectIcon
    icon_color: str | None
    position: int


class Thread(TypedDict):
    id: str
    conversation_id: str
    title: str
    project_id: str | None
    folder: str | None
    model: ModelChoice
    # "full": commands the agent asks to run outside the sandbox run without a card.
    mode: Literal["auto", "plan", "full"]
    instructions: str
    archived: bool
    pinned: bool
    created_at: str
    updated_at: str
    parent_thread_id: str | None
    parent_run_id: str | None
    worktree: bool
    worktree_status: Literal["active", "deleted"] | None
    worktree_available: bool


class IntervalTiming(TypedDict):
    kind: Literal["interval"]
    every_seconds: int


class WeeklyTiming(TypedDict):
    kind: Literal["weekly"]
    time: str
    days: list[int]


class MonthlyTiming(TypedDict):
    kind: Literal["monthly"]
    time: str
    day: int


class OnceTiming(TypedDict):
    kind: Literal["once"]
    at: str


RoutineTiming = Annotated[
    IntervalTiming | WeeklyTiming | MonthlyTiming | OnceTiming, Field(discriminator="kind")
]


class Routine(TypedDict):
    """A chat sent `instruction` on a schedule while the app is open."""

    instruction: str
    timing: RoutineTiming
    # The timing in words: "on weekdays at 09:00".
    summary: str
    enabled: bool
    # The chat each finished run is reported to, if any.
    report_to: str | None
    last_run_at: str | None
    # None when paused, or when a one-off has run.
    next_run_at: str | None
    last_error: str | None
    finished: bool


class SidebarThread(Thread):
    """A chat as the sidebar lists it: with its routine and the state of its runs."""

    routine: Routine | None
    configured_folder: str | None
    status: Literal["idle"] | RunStatus
    active_run_id: str | None
    last_run_id: str | None
    last_finished_run_id: str | None
    # Paused on questions rather than an approval card.
    asks_questions: bool
    # The pause it waits on: one pause can follow another without the status changing.
    pause_sequence: int | None


class SearchResult(SidebarThread):
    excerpt: str


class WorkspaceSnapshot(TypedDict):
    agent_id: str
    projects: list[Project]
    threads: list[SidebarThread]
    access: Access
    default_mode: Literal["auto", "plan", "full"]


class AccessSetting(TypedDict):
    access: Access


class DefaultMode(TypedDict):
    mode: Literal["auto", "plan", "full"]


class WorktreeCleanup(TypedDict):
    retention_days: int | None


class ProjectFile(TypedDict):
    id: str
    project_id: str
    name: str
    media_type: str
    relative_path: str
    size_bytes: int
    created_at: str


class UploadedFile(ProjectFile):
    extracted_text: str


class RunPage(TypedDict):
    runs: list[ChatRun]
    older_cursor: str | None


class ThreadDetail(Thread):
    configured_folder: str | None
    runs: list[ChatRun]
    older_cursor: str | None
    attachments: list[ProjectFile]


class Draft(TypedDict):
    """The message a fork or rewind put back in the composer."""

    content: str
    files: list[ReferenceFile]
    context: CodingContext


class ForkedThread(Thread):
    draft: Draft | None


class Rewound(TypedDict):
    thread: Thread
    draft: Draft | None
    archived_thread_id: str


class AnalyticsMetric(TypedDict):
    value: Num | None
    known_runs: int


class AnalyticsStats(TypedDict):
    runs: int
    completed_runs: int
    failed_runs: int
    cancelled_runs: int
    active_runs: int
    user_requests: int
    last_active_at: str | None
    input_tokens: AnalyticsMetric
    output_tokens: AnalyticsMetric
    total_tokens: AnalyticsMetric
    cached_input_tokens: AnalyticsMetric
    uncached_input_tokens: AnalyticsMetric
    cost_usd: AnalyticsMetric
    duration_ms: AnalyticsMetric
    average_duration_ms: AnalyticsMetric


class ModelAnalytics(AnalyticsStats):
    provider: str | None
    model: str | None


class DailyAnalytics(AnalyticsStats):
    date: str


class Analytics(TypedDict):
    generated_at: str
    timezone: Literal["UTC"]
    totals: AnalyticsStats
    models: list[ModelAnalytics]
    daily: list[DailyAnalytics]


class TreeEntry(TypedDict):
    name: str
    path: str
    directory: bool


class FileText(TypedDict):
    ok: bool
    text: str
    version: str


class ContextFiles(TypedDict):
    ok: bool
    # The first 200 matches; `more` says there are others.
    files: list[str]
    more: bool


class PromptTemplate(TypedDict):
    name: str
    description: str
    body: str


class Workflow(TypedDict):
    prompts: list[PromptTemplate]
    scope: Literal["project", "conversation"]
    version: str


class GitFile(TypedDict):
    path: str
    original_path: str | None
    index: str
    worktree: str
    digest: str
    staged: bool
    unstaged: bool
    conflict: bool


class GitCommit(TypedDict):
    sha: str
    title: str
    author: str
    date: str


class GitHubRepository(TypedDict):
    owner: str
    repo: str


class GitIdentity(TypedDict):
    name: str
    email: str


class GitState(TypedDict):
    ok: bool
    version: str
    head: str
    branch: str
    # The first 5,000 changes; total_files counts them all.
    files: list[GitFile]
    total_files: int
    conflicted: bool
    upstream: str
    ahead: int
    behind: int
    remote: str | None
    github: GitHubRepository | None
    default_base: str
    branches: list[str]
    history: list[GitCommit]
    identity: GitIdentity


class GitBranch(TypedDict):
    name: str
    ref: str


class GitBranches(TypedDict):
    ok: bool
    current: str | None
    head: str
    default: str | None
    branches: list[GitBranch]


class GitHunk(TypedDict):
    id: str
    header: str
    patch: str


class GitDiff(TypedDict):
    ok: bool
    diff: str
    hunks: list[GitHunk]
    warning: str | None


class TerminalSession(TypedDict):
    id: str
    thread_id: str
    title: str
    cwd: str
    command: str | None
    owner: Literal["user", "agent"]
    run_id: str | None
    cols: int
    rows: int
    created_at: str
    status: Literal["running", "exited", "closed"]
    exit_code: int | None


class TerminalOutput(TerminalSession):
    text: str
    cursor: int
    reset: bool


class FilePreview(TypedDict):
    path: str
    name: str
    kind: Literal["pdf", "image", "table", "text", "other"]
    size: int
    previewable: bool
    # False for files that would run when opened; those can only be shown in Finder.
    openable: bool
