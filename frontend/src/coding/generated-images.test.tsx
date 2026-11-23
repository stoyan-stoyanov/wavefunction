import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { makeEvent, makeRun } from "@/test/fixtures";
import { codingApi } from "./api";
import { FileLinks } from "./file-links";
import { GeneratedImages } from "./generated-images";
import { runTranscript } from "./run-transcript";

it("shows the images a tool saved where it saved them and opens each at full size", async () => {
  const saved = {
    model: "gemini::gemini-nano-banana-2.1",
    images: [{ path: "generated-images/fox.png" }, { attachment: "cat.png", id: "file-1" }],
  };
  const run = makeRun({
    events: [
      makeEvent({
        id: "started",
        event_type: "tool_started",
        payload: { id: "make", tool: "generate_image" },
      }),
      makeEvent({
        id: "made",
        event_type: "tool_completed",
        payload: { id: "make", tool: "generate_image", result: JSON.stringify(saved) },
      }),
      // A failed call shows no images.
      makeEvent({
        id: "refused",
        event_type: "tool_failed",
        payload: { id: "again", tool: "edit_image", result: "OpenAI refused the image request" },
      }),
    ],
  });
  const items = runTranscript(run);
  expect(items).toEqual([{ kind: "images", id: "made", images: saved.images }]);
  const item = items[0] as Extract<(typeof items)[number], { kind: "images" }>;

  vi.spyOn(codingApi, "filePreviewUrl").mockResolvedValue("blob:fox");
  vi.spyOn(codingApi, "attachmentPreview").mockResolvedValue("blob:cat-thumbnail");
  const full = vi.spyOn(codingApi, "attachmentImage").mockResolvedValue("blob:cat");
  const openFile = vi.fn();
  render(
    <FileLinks.Provider value={openFile}>
      <GeneratedImages threadId="thread-a" images={item.images} />
    </FileLinks.Provider>,
  );
  expect(await screen.findByRole("img", { name: "fox.png" })).toHaveAttribute("src", "blob:fox");
  expect(await screen.findByRole("img", { name: "cat.png" })).toHaveAttribute("src", "blob:cat-thumbnail");

  // A file in the chat's folder opens in the chat's file preview.
  fireEvent.click(screen.getByRole("button", { name: "Open fox.png" }));
  expect(openFile).toHaveBeenCalledWith("generated-images/fox.png");
  // An attached image opens here, whole.
  fireEvent.click(screen.getByRole("button", { name: "Open cat.png" }));
  const dialog = await screen.findByRole("dialog", { name: "cat.png" });
  expect(await within(dialog).findByRole("img", { name: "cat.png" })).toHaveAttribute("src", "blob:cat");
  expect(full).toHaveBeenCalledWith("thread-a", "file-1", expect.any(AbortSignal));
});
