import { A, C, DocFooter, DocHeader, Figure, H2, List, P, Table, UI } from "@/components/docs";
import { Mark } from "@/components/feature-visuals";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/models",
  "Connect a model: ChatGPT, API keys, Ollama and local servers",
  "Use Wavefunction with a ChatGPT sign-in, an API key from OpenAI, Anthropic, Google Gemini, OpenRouter or Vercel AI Gateway, or local models through Ollama and LM Studio.",
);

function Provider({ logo, name, href }: { logo?: string; name: string; href?: string }) {
  return (
    <span className="flex items-center gap-2.5">
      {logo ? <Mark name={logo} className="size-4 shrink-0" /> : <span className="size-4 shrink-0" />}
      {href ? <A href={href}>{name}</A> : name}
    </span>
  );
}

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Connect a model"
        lede="Wavefunction works with the models you already use: a ChatGPT sign-in, an API key, or a model running on your own computer. Connect as many as you like and switch between them in any chat."
        updated="November 26, 2026"
      />

      <H2 id="where">Where to connect one</H2>
      <P>
        Setup’s first step, <UI>Connect a provider</UI>, connects your first one. Add more later in{" "}
        <UI>Settings › Models › Add provider</UI>.
      </P>
      <Figure
        src="/wavefunction-providers.jpg"
        alt="Wavefunction setup, step 1: connect a provider, from ChatGPT, Anthropic, Google Gemini, OpenRouter, Vercel AI Gateway, Ollama or an OpenAI-compatible server."
        width={2841}
        height={1856}
        caption="Setup’s first step: connect a provider."
      />

      <H2 id="providers">Providers</H2>
      <Table
        head={["Provider", "How it connects"]}
        rows={[
          [<Provider key="c" logo="openai" name="ChatGPT" href="/docs/chatgpt" />, "Sign in with a Plus or Pro plan."],
          [<Provider key="a" logo="anthropic" name="Anthropic" />, "API key."],
          [<Provider key="g" logo="gemini" name="Google Gemini" />, "API key from Google AI Studio."],
          [<Provider key="p" logo="openai" name="OpenAI" />, "API key."],
          [<Provider key="r" logo="openrouter" name="OpenRouter" />, "API key. Lists only models that support tool calling."],
          [<Provider key="v" logo="vercel" name="Vercel AI Gateway" />, "API key."],
          [<Provider key="o" logo="ollama" name="Ollama" href="/docs/ollama" />, "Models on your computer or your own server."],
          [
            <Provider key="l" name="OpenAI-compatible servers" href="/docs/lm-studio" />,
            "LM Studio and other servers with an OpenAI-compatible API.",
          ],
        ]}
      />
      <P>Anthropic, Google Gemini and OpenRouter keys are checked when you save them.</P>

      <H2 id="choosing">Choosing a model</H2>
      <List>
        <li>
          The <UI>Default model</UI> in <UI>Settings › Models</UI> is used for new chats.
        </li>
        <li>Switch a chat’s model at any time from the model menu in its message box.</li>
        <li>The helpers the agent starts for parallel work run on your enabled models.</li>
      </List>
      <Figure
        src="/wavefunction-choose-models.jpg"
        alt="Wavefunction setup, step 2: choose models, with a list of ChatGPT models to tick."
        width={2000}
        height={1077}
        caption="Setup’s second step: pick the models you want in chats."
      />

      <H2 id="requirements">What a model needs</H2>
      <List>
        <li>
          <UI>Tool calling</UI>, to run commands and change code. Ollama models without it are marked
          and can’t be enabled.
        </li>
        <li>
          <UI>Image input</UI>, for <A href="/docs/computer-use">computer use</A>. A model without it
          can’t see the agent’s screen.
        </li>
      </List>

      <H2 id="web-search">Web search</H2>
      <P>
        Chats on ChatGPT, OpenRouter, Vercel AI Gateway, Anthropic or Gemini search the web through
        that provider, with nothing to set up. For OpenAI, Ollama or another compatible server, add a{" "}
        <UI>Tavily API key</UI> under <UI>Settings › Models › Web search</UI>.
      </P>

      <H2 id="keys">Keys and zero data retention</H2>
      <P>
        Keys are saved in a file in Wavefunction’s data folder that only your user account can read.
        They aren’t encrypted. An environment variable with the same name, such as{" "}
        <C>OPENAI_API_KEY</C> or <C>ANTHROPIC_API_KEY</C>, takes precedence over the saved key.
      </P>
      <P>
        <UI>Require zero data retention</UI>, under <UI>Privacy</UI> at the top of{" "}
        <UI>Settings › Models</UI>, sends OpenRouter and Vercel AI Gateway chats only to providers
        that keep no prompts or responses. It also covers image generation and web search, and
        applies from your next message. Other providers set this on your account with them.
      </P>

      <DocFooter path="/docs/models" />
    </article>
  );
}
