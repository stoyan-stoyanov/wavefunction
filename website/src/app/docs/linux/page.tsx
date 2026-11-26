import { A, C, Code, DocFooter, DocHeader, H2, List, Note, P, UI } from "@/components/docs";
import { docMetadata } from "@/lib/docs";
import { site } from "@/lib/site";

export const metadata = docMetadata(
  "/docs/linux",
  "Wavefunction on Linux: an open-source coding agent for Ubuntu and Debian",
  "Install Wavefunction, a free, open-source desktop coding agent, on Linux with an AppImage or a .deb for Ubuntu 22.04, Debian 12 and later.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Wavefunction on Linux"
        lede="Wavefunction runs on x86-64 Linux, with an AppImage and a .deb for Ubuntu 22.04, Debian 12 and later. It’s the same app as on the Mac: parallel chats, any model and computer use."
        updated="November 26, 2026"
      />
      <Note>
        Linux support is experimental and community-supported, and Linux downloads start with version
        0.6.0. If something breaks, please <A href={site.bugReport}>report it</A>.
      </Note>

      <H2 id="download">Download</H2>
      <P>
        Get the AppImage or the <C>.deb</C> from the <A href={site.releases}>latest release</A> on
        GitHub. They aren’t signed, so check them against the release’s <C>SHA256SUMS-linux</C>:
      </P>
      <Code>{`sha256sum -c --ignore-missing SHA256SUMS-linux`}</Code>

      <H2 id="appimage">Install the AppImage</H2>
      <P>
        Install Bubblewrap, which the app’s sandbox needs, then make the AppImage executable and run
        it:
      </P>
      <Code>{`sudo apt install bubblewrap
chmod +x Wavefunction-<version>-linux-x86_64.AppImage
./Wavefunction-<version>-linux-x86_64.AppImage`}</Code>
      <P>
        The AppImage updates itself from <UI>Settings › About</UI>, so keep it in a folder you can
        write to, such as <C>~/Applications</C>.
      </P>

      <H2 id="deb">Install the .deb</H2>
      <Code>{`sudo apt install ./Wavefunction-<version>-linux-x86_64.deb`}</Code>
      <P>
        This also installs Bubblewrap. The <C>.deb</C> doesn’t update itself: install each new
        release’s <C>.deb</C> the same way.
      </P>

      <H2 id="differences">What’s different on Linux</H2>
      <List>
        <li>
          Commands run in a Bubblewrap sandbox with a seccomp filter, from the same grants as on the
          Mac. Without <C>/usr/bin/bwrap</C>, the app runs no commands at all.
        </li>
        <li>
          <A href="/docs/computer-use">Computer use</A> needs Docker Engine and a model that can read
          images.
        </li>
        <li>Dictation is macOS-only for now.</li>
        <li>
          Your chats and settings live in <C>~/.local/share/app.harness.coding</C>.
        </li>
      </List>

      <H2 id="build">Build it yourself</H2>
      <P>
        For arm64, or to build from source on Ubuntu 24.04 or later, follow the steps in the{" "}
        <A href={`${site.github}#linux-experimental`}>README</A>.
      </P>

      <DocFooter path="/docs/linux" />
    </article>
  );
}
