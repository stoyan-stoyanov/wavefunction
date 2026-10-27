import { expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DiffView } from "./diff";
import { parseDiff } from "./diff-parser";

const patch = "--- a/app.py\n+++ b/app.py\n@@ -4,2 +4,3 @@\n keep\n-old\n+new\n+extra\n";
it("keeps before/after line anchors across asymmetric hunks", () => {
  expect(parseDiff(patch).filter((line) => line.kind !== "meta" && line.kind !== "hunk")).toEqual([
    { kind: "context", text: "keep", before: 4, after: 4 },
    { kind: "delete", text: "old", before: 5, after: null },
    { kind: "add", text: "new", before: null, after: 5 },
    { kind: "add", text: "extra", before: null, after: 6 },
  ]);
  // A deleted SQL comment ("-- users") reads "--- users" inside a hunk; it is not a file header.
  const sql =
    "--- a/s.sql\n+++ b/s.sql\n@@ -1,3 +1,2 @@\n--- users\n create table users (\n-  name text\n+  full_name text\n";
  expect(
    parseDiff(sql)
      .slice(3)
      .map(({ kind, before, after }) => [kind, before, after]),
  ).toEqual([
    ["delete", 1, null],
    ["context", 2, 1],
    ["delete", 3, null],
    ["add", null, 2],
  ]);
});
it("attaches split diff feedback to the selected saved side and line", () => {
  const onComment = vi.fn();
  render(<DiffView diff={patch} split onComment={onComment} />);
  fireEvent.click(screen.getByRole("button", { name: "Comment on before line 5" }));
  fireEvent.click(screen.getByRole("button", { name: "Comment on after line 6" }));
  expect(onComment.mock.calls).toEqual([[{ side: "before", line: 5 }], [{ side: "after", line: 6 }]]);
  expect(screen.getByLabelText("Split diff")).toHaveTextContent("extra");
});
