import { ComputerVisual } from "@/components/computer-visual";
import { A, DocFooter, DocHeader, H2, List, P, UI } from "@/components/docs";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/computer-use",
  "Computer use in a local Docker container",
  "Wavefunction's agent gets a desktop of its own in a local Docker container, with a browser, so it can click through websites without taking over your mouse, keyboard or screen.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Computer use"
        lede="When a task needs a browser or a desktop, the agent gets one of its own in a local Docker container. It never moves your pointer, types on your keyboard or records your screen."
        updated="November 26, 2026"
      />
      <div className="mt-8">
        <ComputerVisual />
      </div>

      <H2 id="requirements">What you need</H2>
      <List>
        <li>
          Docker Desktop on a Mac, or Docker Engine on Linux. Docker is installed separately and stays
          under your control.
        </li>
        <li>
          A model that can read images, so the agent can see its screen. See{" "}
          <A href="/docs/models">Connect a model</A>.
        </li>
      </List>
      <P>
        <UI>Settings › Computer use</UI> shows whether Docker is installed and running.
      </P>

      <H2 id="how-it-works">How it works</H2>
      <List>
        <li>
          When a task first needs the desktop, the chat shows <UI>Setting up computer use</UI>, and
          the task carries on once the desktop is ready.
        </li>
        <li>
          The desktop is a Linux desktop with a browser. It can reach the internet, but not your
          files, screen or keychain.
        </li>
        <li>Watch it work, or take control at any time.</li>
        <li>Servers the chat starts in its terminal open in the desktop at their address.</li>
      </List>

      <H2 id="sign-ins">Sign-ins</H2>
      <P>
        When a site needs your login, you sign in privately. The agent pauses and never sees what you
        type. Each chat keeps its own browser profile, so it stays signed in for next time.
      </P>

      <H2 id="files">Files in and out</H2>
      <P>
        A file reaches your computer only when the agent hands it over, to a folder you allowed.
        Files you add to the chat are ready to upload.
      </P>

      <DocFooter path="/docs/computer-use" />
    </article>
  );
}
