import { A, DocFooter, DocHeader, Figure, H2, List, Note, P, UI } from "@/components/docs";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/chatgpt",
  "Use your ChatGPT Plus or Pro plan for coding",
  "Sign in to Wavefunction, a free, open-source desktop coding agent, with your ChatGPT Plus or Pro plan and code without an API key.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Sign in with ChatGPT"
        lede="Use the ChatGPT plan you already pay for. Sign in with a Plus or Pro account, and your chats run on your plan, with no API key."
        updated="November 26, 2026"
      />

      <H2 id="sign-in">Sign in</H2>
      <List ordered>
        <li>
          Open <UI>Settings › Models › Add provider</UI> and choose <UI>ChatGPT</UI>, or pick it in
          setup’s first step.
        </li>
        <li>
          Click <UI>Sign in with ChatGPT</UI>. Your browser opens OpenAI’s sign-in page.
        </li>
        <li>Sign in there within 5 minutes, then come back to Wavefunction.</li>
      </List>
      <P>The models your account can use then appear in Wavefunction.</P>
      <Figure
        src="/wavefunction-choose-models.jpg"
        alt="Wavefunction setup, step 2: choose models, with a list of ChatGPT models to tick."
        width={2000}
        height={1077}
        caption="After you sign in, pick the ChatGPT models you want in chats."
      />

      <H2 id="models">Models</H2>
      <P>
        Wavefunction reads the list of models from your account. To use one that isn’t listed, add
        its ID with <UI>Use another model ID</UI>.
      </P>
      <P>Web search goes through ChatGPT too, with nothing to set up.</P>

      <H2 id="good-to-know">Good to know</H2>
      <List>
        <li>
          The sign-in comes back to your computer on port 1455. If something else is using it,
          Wavefunction says so: quit the Codex CLI or any other copy of Wavefunction, then try again.
        </li>
        <li>Your sign-in is saved in a file that only your user account can read.</li>
      </List>
      <Note>
        This signs in the way the Codex CLI does, which OpenAI hasn’t published for other apps, so it
        can stop working. An <A href="/docs/models">API key</A> is the alternative.
      </Note>

      <DocFooter path="/docs/chatgpt" />
    </article>
  );
}
