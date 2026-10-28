import { invoke } from "@tauri-apps/api/core";
import { codingApi } from "@/coding/api";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, fetchBlobUrl, inspectLocalIntegration, streamRunEvents } from "./api";
import { request } from "./transport";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
});

describe("request destinations", () => {
  it("authenticates native JSON, uploads, streams and screenshot requests", async () => {
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    vi.mocked(invoke).mockResolvedValue({
      base_url: "http://127.0.0.1:8765",
      token: "device-token",
    });
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/events?")) return new Response('id: 1\ndata: {"sequence":1,"type":"done"}\n\n');
      if (url.endsWith("/screenshot.png")) return new Response("file");
      return Response.json({});
    });
    await codingApi.workspace();
    await api.getConnections();
    await codingApi.upload("thread-a", new File(["image"], "photo.png"));
    await api.listGrants();
    await codingApi.access("network");
    await inspectLocalIntegration("gog");
    const events = vi.fn();
    await streamRunEvents("r", 0, events);
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = vi.fn().mockReturnValue("blob:fixture");
        static revokeObjectURL = vi.fn();
      },
    );
    await fetchBlobUrl("/screenshot.png");
    expect(events).toHaveBeenCalledOnce();
    for (const [url, init] of fetcher.mock.calls) {
      if (String(url).endsWith("/health")) continue;
      const headers = init?.headers as Headers;
      expect(String(url)).toMatch(/^http:\/\/127\.0\.0\.1:8765\//);
      expect(headers.get("Authorization")).toBe("Bearer device-token");
      expect(init).toMatchObject({ redirect: "error", credentials: "omit" });
    }
    expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:8765/api/grants", expect.anything());
    expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:8765/api/coding/access", expect.anything());
    expect(fetcher).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/api/local-integrations/gog",
      expect.anything(),
    );
    const upload = fetcher.mock.calls.find(([url]) =>
      String(url).endsWith("/threads/thread-a/attachments"),
    )![1]!;
    expect(upload.body).toBeInstanceOf(FormData);
    expect((upload.headers as Headers).has("Content-Type")).toBe(false);
  });

  it("rejects external request URLs before sending credentials", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    await expect(request("https://other.test/api")).rejects.toThrow("backend-relative");
    await expect(request("//other.test/api")).rejects.toThrow("backend-relative");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects a late response even when the underlying fetch ignores abort", async () => {
    let finish!: (response: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const controller = new AbortController();
    const result = request("/api/coding/workspace", { signal: controller.signal });
    await vi.waitFor(() => expect(finish).toBeDefined());
    const rejected = expect(result).rejects.toMatchObject({
      name: "AbortError",
    });
    controller.abort();
    finish(Response.json([{ id: "same-id", name: "Old workspace" }]));
    await rejected;
    expect(controller.signal.aborted).toBe(true);
  });

  it("supports caller cancellation without newer WebKit abort methods and releases its listeners", async () => {
    vi.spyOn(AbortSignal, "any").mockImplementation(() => {
      throw new Error("Unavailable in this WebView");
    });
    vi.spyOn(AbortSignal.prototype, "throwIfAborted").mockImplementation(() => {
      throw new Error("Unavailable in this WebView");
    });
    const caller = new AbortController();
    const listen = vi.spyOn(caller.signal, "addEventListener");
    const unlisten = vi.spyOn(caller.signal, "removeEventListener");
    const cancel = vi.fn();
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(c) {
            stream = c;
          },
          cancel,
        }),
      ),
    );
    const event = vi.fn();
    const following = streamRunEvents("same-id", 0, event, caller.signal);
    const rejected = expect(following).rejects.toMatchObject({
      name: "AbortError",
    });
    stream.enqueue(new TextEncoder().encode('data: {"sequence":1}\n\n'));
    await vi.waitFor(() => expect(event).toHaveBeenCalledOnce());
    caller.abort();
    await rejected;
    expect(cancel).toHaveBeenCalledOnce();
    expect(unlisten).toHaveBeenCalledWith("abort", listen.mock.calls[0][1]);
  });
});

it("reads every event when a network read ends between a frame's closing CR and LF", async () => {
  // The backend ends each line with "\r\n", and each frame with one more.
  const frame = (sequence: number) => `id: ${sequence}\r\ndata: {"sequence":${sequence}}\r\n\r\n`;
  const sent = frame(1) + frame(2);
  const split = frame(1).length - 1;
  const encoder = new TextEncoder();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      new ReadableStream({
        start(stream) {
          stream.enqueue(encoder.encode(sent.slice(0, split)));
          stream.enqueue(encoder.encode(sent.slice(split)));
          stream.close();
        },
      }),
    ),
  );
  const events = vi.fn();
  await streamRunEvents("r", 0, events);
  expect(events.mock.calls.map(([event]) => event)).toEqual([{ sequence: 1 }, { sequence: 2 }]);
});
