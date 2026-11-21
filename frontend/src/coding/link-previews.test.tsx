import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { codingApi } from "./api";
import { ChatFileLinks } from "./link-previews";
import { CodingMarkdown } from "./markdown";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn().mockResolvedValue(undefined) }));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const card = {
  kind: "page" as const,
  title: "Wave guide",
  description: "How waves <b>work</b>",
  site_name: "Example Docs",
  image: "https://docs.example.com/cover.png",
  icon: "https://docs.example.com/favicon.ico",
};

function reply(text: string, open = vi.fn()) {
  render(
    <ChatFileLinks threadId="chat-1" open={open}>
      <CodingMarkdown>{text}</CodingMarkdown>
    </ChatFileLinks>,
  );
  return open;
}

it("asks the engine for cards of the web pages a reply links, never for code or this machine", async () => {
  vi.stubGlobal("open", vi.fn());
  const linkPreview = vi
    .spyOn(codingApi, "linkPreview")
    .mockImplementation(async (url) =>
      url.includes("docs.example.com")
        ? { url, ...card }
        : { url, kind: "none", title: null, description: null, site_name: null, image: null, icon: null },
    );
  const linkMedia = vi.spyOn(codingApi, "linkMedia").mockImplementation(async (url) => `blob:${url}`);
  reply(
    "Read [the guide](https://docs.example.com/guide), see https://dead.example.com/page. " +
      "and run `curl https://inline.example.com/x`. The app is at http://localhost:5173/app\n\n" +
      "```sh\ncurl https://fenced.example.com/y\n```",
  );
  const guide = await screen.findByRole("link", { name: "Open Wave guide" });
  // What the page says about itself is text, never markup.
  expect(guide).toHaveTextContent("Example Docs");
  expect(guide).toHaveTextContent("How waves <b>work</b>");
  await waitFor(() => expect(linkMedia).toHaveBeenCalledWith(card.image, expect.anything()));
  expect(linkPreview.mock.calls.map(([url]) => url)).toEqual([
    "https://docs.example.com/guide",
    "https://dead.example.com/page",
  ]);
  // A server on this machine gets a simple card without a fetch; a page without a card, none.
  expect(screen.getByRole("link", { name: "Open localhost:5173" })).toHaveTextContent("localhost:5173/app");
  expect(screen.queryByRole("link", { name: /Open .*dead/ })).toBeNull();
  expect(screen.getByRole("link", { name: "https://dead.example.com/page" })).toBeVisible();
  fireEvent.click(guide);
  expect(window.open).toHaveBeenCalledWith("https://docs.example.com/guide", "_blank", "noopener");
});

it("shows pictures as thumbnails that open larger and plays a video only when asked", async () => {
  const files = vi.spyOn(codingApi, "filePreviewUrl").mockImplementation(async (_chat, path) => {
    if (path.includes("missing")) throw new Error("File not found");
    return `blob:${path}`;
  });
  vi.spyOn(codingApi, "linkMedia").mockImplementation(async (url) => `blob:${url}`);
  const open = reply(
    "Here is ![the chart](out/chart.png) and ![a lost one](out/missing.png).\n\n" +
      "Recorded [the demo](out/demo.mp4); the logo is https://cdn.example.com/logo.png",
  );
  // A file opens in the chat's file preview; a web picture in a viewer.
  fireEvent.click(await screen.findByRole("button", { name: "View the chart" }));
  expect(open).toHaveBeenCalledWith("out/chart.png");
  expect(files).toHaveBeenCalledWith("chat-1", "out/chart.png", expect.anything());
  expect(await screen.findByText("a lost one")).toBeVisible();
  const logo = await screen.findByRole("button", { name: "View logo.png" });
  await waitFor(() => expect(logo).toBeEnabled());
  fireEvent.click(logo);
  const viewer = await screen.findByRole("dialog", { name: "logo.png" });
  expect(within(viewer).getByRole("img", { name: "logo.png" })).toHaveAttribute(
    "src",
    "blob:https://cdn.example.com/logo.png",
  );
  fireEvent.click(within(viewer).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  // The video downloads when played, and plays in place.
  expect(files).not.toHaveBeenCalledWith("chat-1", "out/demo.mp4", expect.anything());
  fireEvent.click(screen.getByRole("button", { name: "Play demo.mp4" }));
  const video = await screen.findByLabelText("demo.mp4", { selector: "video" });
  expect(video).toHaveAttribute("src", "blob:out/demo.mp4");
});
