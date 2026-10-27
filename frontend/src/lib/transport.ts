import { invoke } from "@tauri-apps/api/core";

interface BackendInfo {
  base_url: string;
  token: string;
}

let backendInfo: Promise<BackendInfo> | undefined;

function localConnection(): Promise<BackendInfo> {
  if (!backendInfo) {
    backendInfo =
      "__TAURI_INTERNALS__" in window
        ? invoke<BackendInfo>("backend_info")
            .then(waitForBackend)
            .catch((error: unknown) => {
              // A failed start is not the app's fate for the rest of the session: the next
              // request asks again rather than replaying the same rejection.
              backendInfo = undefined;
              throw error;
            })
        : Promise.resolve({ base_url: "", token: "" });
  }
  return backendInfo;
}

/** Allow slow first launches, including macOS verification of a newly built app.
 * A sidecar that exits is reported immediately; this deadline applies only while
 * the engine is still alive and starting. */
const START_DEADLINE_MS = 120_000;
const START_POLL_MS = 250;

interface BackendStatus {
  /** Set once the sidecar has exited: how, and the last lines it wrote. */
  exited: string | null;
}

async function waitForBackend(info: BackendInfo): Promise<BackendInfo> {
  const deadline = Date.now() + START_DEADLINE_MS;
  let checkedExit = 0;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${info.base_url}/health`);
      if (response.ok) return info;
    } catch {
      // A refused connection is the normal answer while the runtime loads.
    }
    if (Date.now() - checkedExit >= 1000) {
      checkedExit = Date.now();
      const status = await invoke<BackendStatus>("backend_status").catch(() => null);
      if (status?.exited) throw new Error(stoppedWhileStarting(status.exited));
    }
    await new Promise((resolve) => window.setTimeout(resolve, START_POLL_MS));
  }
  throw new Error(
    "The local agent engine did not answer within two minutes. Restart Wavefunction and try again.",
  );
}

/** "exit code 1" and the tail of stderr, as one sentence: the last line of a traceback is the
 * one that names the problem. */
function stoppedWhileStarting(exited: string): string {
  const [how, ...tail] = exited.split("\n");
  const reason = tail.filter((line) => line.trim()).pop();
  return reason
    ? `The local agent engine stopped while starting (${how}): ${reason.trim()}`
    : `The local agent engine stopped while starting (${how}). Restart Wavefunction and try again.`;
}

/** What the server actually said, whatever shape it said it in.
 *
 * FastAPI answers a validation failure with `detail` as a *list* of objects, not a string. Read
 * as a string that is `[object Object]` — the error banner said nothing at all, which is worse
 * than the error. */
function readDetail(body: unknown): string {
  if (typeof body !== "object" || body === null) return "";
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        typeof item === "string"
          ? item
          : typeof item === "object" && item !== null && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : "",
      )
      .filter(Boolean)
      // Pydantic prefixes its own errors; the sentence after it is the one written for a person.
      .map((message) => message.replace(/^Value error,\s*/, ""));
    if (messages.length) return messages.join(". ");
  }
  return "";
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

/** Compatible with the WebKit versions supported by the desktop. */
export function throwIfAborted(signal: AbortSignal) {
  if (signal.aborted) throw signal.reason ?? new DOMException("The request was cancelled.", "AbortError");
}

export async function requestResponse<T>(
  path: string,
  consume: (response: Response, signal: AbortSignal) => Promise<T>,
  init: RequestInit = {},
): Promise<T> {
  // Never attach the local bearer token to a response-provided external URL.
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\"))
    throw new Error("Expected a backend-relative path.");
  const signal = init.signal ?? new AbortController().signal;
  throwIfAborted(signal);
  const backend = await localConnection();
  throwIfAborted(signal);
  const headers = new Headers(init.headers);
  headers.delete("Authorization");
  if (backend.token) headers.set("Authorization", `Bearer ${backend.token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${backend.base_url}${path}`, {
      ...init,
      headers,
      signal,
      credentials: "omit",
      redirect: "error",
    });
  } catch (error) {
    throwIfAborted(signal);
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(
      "The local agent engine closed the request unexpectedly. Restart Wavefunction and try again.",
      0,
    );
  }
  throwIfAborted(signal);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      message = readDetail(await response.json()) || message;
    } catch {
      /* Keep status if not JSON. */
    }
    throwIfAborted(signal);
    throw new ApiError(message, response.status);
  }
  const result = await consume(response, signal);
  throwIfAborted(signal);
  return result;
}

/** A websocket to a backend path. A browser cannot set a header on a websocket, so the local
 * bearer token travels as a subprotocol beside the protocols asked for, not in the URL. */
export async function openSocket(path: string, protocols: string[] = []): Promise<WebSocket> {
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error("Expected a backend-relative path.");
  const backend = await localConnection();
  const base = backend.base_url || window.location.origin;
  const url = base.replace(/^http/, "ws") + path;
  return new WebSocket(url, backend.token ? [...protocols, `bearer.${backend.token}`] : protocols);
}

export function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return requestResponse(
    path,
    async (response) => (response.status === 204 ? undefined : await response.json()),
    init,
  );
}
