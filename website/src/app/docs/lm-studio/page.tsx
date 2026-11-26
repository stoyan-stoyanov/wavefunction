import { A, C, DocFooter, DocHeader, H2, List, P, UI } from "@/components/docs";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/lm-studio",
  "Use LM Studio as a local coding agent",
  "Connect Wavefunction, a free, open-source desktop coding agent, to LM Studio or any OpenAI-compatible server and run it on local models.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Use Wavefunction with LM Studio"
        lede="LM Studio, and any other server with an OpenAI-compatible API, can run the agent’s models on your own computer."
        updated="November 26, 2026"
      />

      <H2 id="before">Before you start</H2>
      <List ordered>
        <li>In LM Studio, download a model that supports tool calling.</li>
        <li>
          Start LM Studio’s local server. By default it listens at <C>http://127.0.0.1:1234</C>.
        </li>
        <li>Note the model’s ID as LM Studio shows it.</li>
      </List>

      <H2 id="connect">Connect it</H2>
      <List ordered>
        <li>
          Open <UI>Settings › Models › Add provider</UI> and choose{" "}
          <UI>OpenAI & compatible servers</UI>.
        </li>
        <li>
          Set <UI>Provider</UI> to <UI>Compatible server</UI>.
        </li>
        <li>
          Enter the model’s ID in <UI>Model ID</UI>.
        </li>
        <li>
          Enter <C>http://127.0.0.1:1234/v1</C> in <UI>API base URL</UI>. The field starts empty.
        </li>
        <li>
          Leave <UI>API key</UI> empty for a local server.
        </li>
        <li>
          Click <UI>Enable model</UI>.
        </li>
      </List>
      <P>
        Wavefunction doesn’t read the server’s model list: add each model by its ID. The model you
        enable last becomes your default.
      </P>

      <H2 id="other-servers">Other servers</H2>
      <P>
        Any server with an OpenAI-compatible API connects the same way, such as llama.cpp’s server or
        vLLM. Use its base URL, ending in <C>/v1</C>, and its API key if it needs one.
      </P>

      <H2 id="good-to-know">Good to know</H2>
      <List>
        <li>Models must support tool calling to make code changes.</li>
        <li>
          Web search needs a <UI>Tavily API key</UI> under <UI>Settings › Models › Web search</UI>.
        </li>
        <li>
          Using Ollama instead? See <A href="/docs/ollama">Ollama</A>.
        </li>
      </List>

      <DocFooter path="/docs/lm-studio" />
    </article>
  );
}
