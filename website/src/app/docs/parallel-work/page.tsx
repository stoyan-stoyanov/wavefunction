import { A, DocFooter, DocHeader, H2, List, P, Panel, UI } from "@/components/docs";
import { Timeline } from "@/components/timeline";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/parallel-work",
  "Run coding agents in parallel with helpers and worktrees",
  "How Wavefunction runs several coding tasks at once: chats side by side, helpers that split a task, Git worktrees that keep them apart, and steering while they work.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Parallel work"
        lede="Give the agent one task after another without waiting. Chats run side by side, a big task splits across helpers, and Git worktrees keep their changes apart."
        updated="November 26, 2026"
      />
      <Panel>
        <Timeline />
      </Panel>

      <H2 id="chats">Chats side by side</H2>
      <P>
        Each chat is its own conversation, with its own model, folder and history, and any number can
        run at once. In a Git repository, a new chat starts on its own branch in its own checkout, so
        chats in one project don’t step on each other. Untick <UI>New worktree</UI> before the first
        message to work in the project’s folder instead.
      </P>

      <H2 id="helpers">Helpers</H2>
      <P>
        For a bigger task, the agent starts helpers: child chats that work in parallel and report
        back. They share the parent chat’s checkout by default. When they could touch the same files,
        each gets an isolated worktree, and the parent reviews their changes and brings them in.
      </P>
      <P>Open a helper to read its transcript, redirect it or stop it.</P>

      <H2 id="steering">Steer while it works</H2>
      <List>
        <li>
          A message you send during a task steers that task. It reaches the helper already on it, so
          the work adjusts without starting over.
        </li>
        <li>
          <UI>Queue follow-up</UI> runs your message after the task instead.
        </li>
        <li>When the agent needs a decision, it asks with a question card.</li>
      </List>

      <H2 id="going-back">Going back</H2>
      <P>
        Fork a chat from any message to try another direction, or rewind to put the code and the
        conversation back to how they were at that point.
      </P>

      <H2 id="long-conversations">Long conversations</H2>
      <P>
        The ring in the message box shows how full the context window is. Near the limit, the
        conversation is summarized, keeping your requests and the confirmed state of the work.{" "}
        <UI>Compact now</UI> does it on demand.
      </P>
      <P>
        To run the same task on a schedule, see <A href="/docs/routines">Scheduled tasks</A>.
      </P>

      <DocFooter path="/docs/parallel-work" />
    </article>
  );
}
