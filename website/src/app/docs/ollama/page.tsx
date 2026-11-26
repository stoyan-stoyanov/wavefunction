import { A, C, Code, DocFooter, DocHeader, Figure, H2, H3, List, Note, P, UI } from "@/components/docs";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/ollama",
  "Run a local coding agent with Ollama",
  "Connect Wavefunction, a free, open-source desktop coding agent, to Ollama and run it on local models on your Mac, Linux machine or your own server.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Use Wavefunction with Ollama"
        lede="Run the agent on models served by Ollama, on your own computer or a server you control. Nothing is billed, and with Ollama on your computer, model requests never leave it."
        updated="November 26, 2026"
      />

      <H2 id="before">Before you start</H2>
      <List ordered>
        <li>
          Install Ollama from <A href="https://ollama.com">ollama.com</A>.
        </li>
        <li>
          Download a model that supports tool calling. Ollama’s library marks these with a{" "}
          <C>tools</C> tag.
          <Code>{`ollama pull <model>`}</Code>
        </li>
      </List>
      <P>
        A local model needs enough memory for itself and its context window. If a model is slow,
        try a smaller one or a smaller context window.
      </P>

      <H2 id="connect">Connect Ollama</H2>
      <List ordered>
        <li>Start Ollama.</li>
        <li>
          In Wavefunction, open <UI>Settings › Models › Add provider</UI> and choose{" "}
          <UI>Ollama</UI>, or pick it in setup’s first step.
        </li>
        <li>
          Leave <UI>Server address</UI> at <C>http://127.0.0.1:11434</C> for Ollama on this computer,
          or enter your server’s address, without <C>/api</C> or <C>/v1</C> at the end.
        </li>
        <li>
          Click <UI>Connect</UI>. Wavefunction lists your Ollama models and what each one supports.
        </li>
        <li>Enable the models you want, then pick one from a chat’s model menu.</li>
      </List>
      <Figure
        src="/wavefunction-providers.jpg"
        alt="Wavefunction setup, step 1: connect a provider, with Ollama among ChatGPT, Anthropic, Google Gemini, OpenRouter, Vercel AI Gateway and OpenAI-compatible servers."
        width={2841}
        height={1856}
        caption="Pick Ollama in setup’s first step, or later in Settings › Models."
      />
      <P>
        Models without tool calling show <UI>Tool calling unavailable — not suitable for agents</UI>{" "}
        and can’t be enabled. After you download another model, click <UI>Refresh models</UI>.
      </P>

      <H2 id="context">Context window</H2>
      <P>
        Wavefunction asks Ollama for a 32,768-token context window, or the model’s own maximum if
        that’s smaller. Change it under <UI>Context size › Context window (tokens)</UI>. A coding
        agent reads many files, so more context helps it, but it also uses more memory.
      </P>

      <H2 id="web-search">Web search</H2>
      <P>
        Ollama chats search the web through Tavily. Add a <UI>Tavily API key</UI> under{" "}
        <UI>Settings › Models › Web search</UI> to turn it on.
      </P>

      <H2 id="troubleshooting">Troubleshooting</H2>
      <H3>It can’t connect</H3>
      <List>
        <li>Check that Ollama is running and reachable at the address you entered.</li>
        <li>
          The address needs <C>http://</C> or <C>https://</C>, and no path, user name or password.
        </li>
        <li>Wavefunction connects directly and ignores system proxy settings.</li>
      </List>
      <H3>A model is missing</H3>
      <P>
        Click <UI>Refresh models</UI>. If the model is there but can’t be enabled, it doesn’t support
        tool calling.
      </P>
      <Note>
        Prefer LM Studio or another server? See <A href="/docs/lm-studio">LM Studio</A>.
      </Note>

      <DocFooter path="/docs/ollama" />
    </article>
  );
}
