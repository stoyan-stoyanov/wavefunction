import { A, C, DocFooter, DocHeader, H2, List, P, UI } from "@/components/docs";
import { PrivacyVisual } from "@/components/feature-visuals";
import { docMetadata } from "@/lib/docs";
import { site } from "@/lib/site";

export const metadata = docMetadata(
  "/docs/privacy",
  "Privacy and security: zero telemetry, local data, sandboxed commands",
  "Wavefunction sends no telemetry, keeps your chats on your computer, runs commands in a sandbox and works with local models or zero-data-retention providers.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Privacy and security"
        lede="Wavefunction collects nothing about you, keeps your work on your computer, and asks before it does anything it can’t undo."
        updated="November 26, 2026"
      />

      <H2 id="telemetry">Zero telemetry</H2>
      <P>
        Wavefunction sends no usage data, analytics or crash reports. What leaves your computer goes
        only to the models, connectors and websites you use, and to GitHub to check for updates.
      </P>
      <div className="mt-8">
        <PrivacyVisual />
      </div>

      <H2 id="models">Keeping model requests private</H2>
      <List>
        <li>
          Use a model on your own computer, through <A href="/docs/ollama">Ollama</A> or{" "}
          <A href="/docs/lm-studio">LM Studio</A>, and model requests stay on it too.
        </li>
        <li>
          Or turn on <UI>Require zero data retention</UI> in <UI>Settings › Models</UI>. OpenRouter
          and Vercel AI Gateway then send chats only to providers that keep no prompts or responses.
          Other providers set this on your account with them.
        </li>
      </List>

      <H2 id="data">Where your data lives</H2>
      <List>
        <li>
          Your conversations, skills and settings stay on your computer: in{" "}
          <C>~/Library/Application Support/app.harness.coding</C> on a Mac and{" "}
          <C>~/.local/share/app.harness.coding</C> on Linux. The folder keeps the app’s identifier
          from before it was named Wavefunction.
        </li>
        <li>
          Provider and connector keys are local files that only your user account can read. They
          aren’t encrypted.
        </li>
      </List>

      <H2 id="sandbox">Sandboxed commands</H2>
      <List>
        <li>
          Commands run in a sandbox by default: the macOS sandbox on a Mac, Bubblewrap on Linux.
          Folders outside your project need your grant.
        </li>
        <li>
          A command that needs to run outside the sandbox asks first, unless you gave that chat{" "}
          <UI>Full access</UI>.
        </li>
        <li>
          A fixed check asks before commands it recognizes as irreversible, such as deletes or force
          pushes. It catches an overeager agent, not every case.
        </li>
        <li>
          <A href="/docs/computer-use">Computer use</A> runs in its own Docker container, away from
          your files, screen and keychain.
        </li>
      </List>

      <H2 id="report">Report a security problem</H2>
      <P>
        Please don’t open a public issue. Follow <A href={site.security}>SECURITY.md</A> to report it
        privately.
      </P>

      <DocFooter path="/docs/privacy" />
    </article>
  );
}
