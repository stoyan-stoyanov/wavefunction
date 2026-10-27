import type { components } from "@/lib/api.gen";

/** What the backend returns, generated from its response models by `npm run generate:api`. */
export type Schemas = components["schemas"];

/** An MCP server the user has connected, and what it is offering. */
export type Connector = Schemas["Connector"];

export type ConnectorProbe = Schemas["ConnectorProbe"];

/** Written guidance the agent can consult. Only the name and description are ever in the prompt;
 *  the body is read when the agent decides the skill applies. */
export type Skill = Schemas["SkillRecord"];

/** A persisted folder grant for the coding agent and terminals. */
export type Grant = Schemas["GrantRecord"];

export type GrantState = Schemas["GrantState"];

/** A model as saved: a chat's keeps the fields its settings sent. */
export type ModelSpec = Schemas["ModelChoice"];

/** A model from the OpenRouter, Vercel AI Gateway, Anthropic or Gemini catalogue. */
export type OpenRouterModel = Schemas["GatewayModel"];

/** A file a chat attached or a message sent. */
export type ProjectFile = Schemas["ReferenceFile"];

export type ComputerStatus = Schemas["ComputerStatus"];

export type RunEvent = Schemas["RunEvent"];

/** Provider-reported totals. Cache and reasoning counts are subsets, not extra tokens. */
export type Usage = Schemas["Usage"];

export type Run = Schemas["Run"];

/** A ChatGPT sign-in: an account rather than a key, so it has its own shape. */
export type CodexStatus = Schemas["CodexStatus"];

export type CodexModel = Schemas["CodexModel"];

export type OllamaConnection = Schemas["OllamaConfig"];

export type Connections = Schemas["Connections"];

/** Connection or local setup required by a waiting task. */
export type RunConnector = Schemas["SetupRow"];
