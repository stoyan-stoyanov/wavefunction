import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CodingMarkdown } from "./markdown";
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn().mockResolvedValue(undefined),
}));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
const table = (rows: number) =>
  "| App | Details |\n| --- | --- |\n" +
  Array.from({ length: rows }, (_, i) => `| App ${i + 1} | Details ${i + 1} |`).join("\n");
it("paginates table rows in tens and preserves revealed rows as the response grows", () => {
  const view = render(<CodingMarkdown>{table(23)}</CodingMarkdown>);
  const body = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
  expect(body()).toHaveLength(10);
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(body()).toHaveLength(20);
  view.rerender(<CodingMarkdown>{table(24)}</CodingMarkdown>);
  expect(body()).toHaveLength(20);
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(body()).toHaveLength(24);
  expect(screen.getByRole("cell", { name: "App 24" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Show less" }));
  expect(body()).toHaveLength(10);
});
it("animates live words but keeps saved and completed messages immediately readable", () => {
  const view = render(<CodingMarkdown>Saved response</CodingMarkdown>);
  expect(view.container.querySelector("[data-sd-animate]")).toBeNull();
  view.rerender(<CodingMarkdown streaming>Live response</CodingMarkdown>);
  expect(view.container.querySelector("[data-sd-animate]")).not.toBeNull();
  view.rerender(<CodingMarkdown streaming>Live response with **new words**</CodingMarkdown>);
  expect(view.container).toHaveTextContent("Live response with new words");
  view.rerender(<CodingMarkdown>Live response with **new words**</CodingMarkdown>);
  expect(view.container.querySelector("[data-sd-animate]")).toBeNull();
  expect(screen.getByText("new words")).toBeVisible();
});
