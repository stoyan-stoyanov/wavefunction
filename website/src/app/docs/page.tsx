import { Download } from "@/components/chrome";
import { A, DocFooter, DocHeader, Figure, H2, List, P, UI } from "@/components/docs";
import { DOCS, docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs",
  "Get started with Wavefunction",
  "Install Wavefunction, the free, open-source coding agent for macOS and Linux, connect a model and start your first chat.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Get started"
        lede="Wavefunction is a free, open-source coding agent for your desktop. Install it, connect a model and start your first chat in a few minutes."
        updated="November 26, 2026"
      />
      <Figure
        src="/wavefunction-new-chat.jpg"
        alt="Wavefunction: projects and their chats in the sidebar, and a new chat ready to start, with the model menu, Full access and New worktree in the message box."
        width={2000}
        height={1084}
      />

      <H2 id="install">Install on a Mac</H2>
      <List ordered>
        <li>Download the installer. You need a Mac with Apple Silicon and macOS 11 or later.</li>
        <li>Open the DMG and drag Wavefunction to Applications.</li>
        <li>Open Wavefunction from Applications.</li>
      </List>
      <div className="mt-6">
        <Download />
      </div>
      <P>
        It’s signed and notarized by Apple, and it updates itself. On Linux, see{" "}
        <A href="/docs/linux">Wavefunction on Linux</A>.
      </P>

      <H2 id="setup">Set up</H2>
      <P>The first time it opens, setup takes you through three steps:</P>
      <List ordered>
        <li>
          <UI>Connect a provider.</UI> Sign in with ChatGPT, paste an API key, or connect Ollama or
          another server on your own computer. <A href="/docs/models">Connect a model</A> compares
          them.
        </li>
        <li>
          <UI>Choose models.</UI> The first one you pick is your default. You can switch models in
          any chat.
        </li>
        <li>
          <UI>Create a project.</UI> Pick a folder, usually a Git repository.
        </li>
      </List>
      <P>Your provider bills model usage. A model on your own computer costs nothing to use.</P>

      <H2 id="projects-and-chats">Projects and chats</H2>
      <P>
        A project is a folder, usually a Git repository, and groups the chats that work in it. Each
        chat is a separate conversation with the agent, with its own model, folder and history, and
        chats run side by side.
      </P>
      <P>
        In a Git repository, a new chat starts on its own branch in its own checkout, so chats in one
        project don’t step on each other. Untick <UI>New worktree</UI> before the first message to
        work in the project’s folder instead.
      </P>

      <H2 id="modes">Modes</H2>
      <List>
        <li>
          By default, commands run in a sandbox, and folders outside your project need your grant. A
          command that needs to run outside the sandbox asks first.
        </li>
        <li>
          <UI>Plan mode</UI> reads and explains without changing anything.
        </li>
        <li>
          <UI>Full access</UI> runs commands outside the sandbox without asking each time. Commands
          that can’t be undone still ask.
        </li>
      </List>

      <H2 id="guides">Guides</H2>
      {DOCS.map(({ group, pages }) => (
        <div key={group}>
          <P>
            <UI>{group}</UI>
          </P>
          <List>
            {pages
              .filter(({ path }) => path !== "/docs")
              .map(({ path, label }) => (
                <li key={path}>
                  <A href={path}>{label}</A>
                </li>
              ))}
          </List>
        </div>
      ))}

      <DocFooter path="/docs" />
    </article>
  );
}
