import type { ModelSpec, Schemas } from "@/lib/types";

// Complete API responses for tests, so a mocked request returns what the backend's response
// model says it does. Each takes the fields a test cares about as overrides.
const stamp = "2026-10-02T00:00:00Z";

export function makeModel(overrides: Partial<ModelSpec> = {}): ModelSpec {
  return {
    provider: "demo",
    model: "demo-reliable",
    base_url: null,
    temperature: 0.1,
    max_tokens: 1600,
    ...overrides,
  };
}

export function makeProject(overrides: Partial<Schemas["Project"]> = {}): Schemas["Project"] {
  return {
    id: "project",
    name: "Project",
    folder: null,
    created_at: stamp,
    icon: "folder",
    icon_color: null,
    position: 0,
    ...overrides,
  };
}

function chat(id: string): Schemas["Thread"] {
  return {
    id,
    conversation_id: `conversation-${id}`,
    title: id,
    project_id: null,
    folder: null,
    model: makeModel({ max_tokens: null }),
    mode: "auto",
    instructions: "",
    archived: false,
    pinned: false,
    created_at: stamp,
    updated_at: stamp,
    parent_thread_id: null,
    parent_run_id: null,
    worktree: false,
    worktree_status: null,
    worktree_available: false,
  };
}

/** A chat as the sidebar lists it. */
export function makeThread(overrides: Partial<Schemas["SidebarThread"]> = {}): Schemas["SidebarThread"] {
  return {
    ...chat(overrides.id ?? "thread"),
    routine: null,
    configured_folder: null,
    status: "idle",
    active_run_id: null,
    last_run_id: null,
    last_finished_run_id: null,
    asks_questions: false,
    pause_sequence: null,
    ...overrides,
  };
}

/** A chat as both the sidebar lists it and opening it returns it, with its latest history. */
export type OpenChat = Schemas["SidebarThread"] & Schemas["ThreadDetail"];

export function makeOpenChat(overrides: Partial<OpenChat> = {}): OpenChat {
  return {
    ...makeThread({ id: overrides.id ?? "thread" }),
    runs: [],
    older_cursor: null,
    attachments: [],
    ...overrides,
  };
}

export function makeEvent(overrides: Partial<Schemas["RunEvent"]> = {}): Schemas["RunEvent"] {
  return {
    id: "event",
    run_id: "run",
    sequence: 1,
    event_type: "text_delta",
    node_id: null,
    payload: {},
    created_at: stamp,
    ...overrides,
  };
}

/** A run as a chat's history returns it. */
export function makeRun(overrides: Partial<Schemas["ChatRun"]> = {}): Schemas["ChatRun"] {
  return {
    id: "run",
    project_id: "agent",
    conversation_id: null,
    status: "completed",
    input: { text: "" },
    output: null,
    error: null,
    started_at: stamp,
    completed_at: null,
    duration_ms: null,
    usage: {},
    kind: "user",
    parent_run_id: null,
    history_order: 1,
    events: [],
    checkpoints: [],
    ...overrides,
  };
}
