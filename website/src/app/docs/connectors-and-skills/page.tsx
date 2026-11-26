import { C, DocFooter, DocHeader, Figure, H2, List, P, Panel, UI } from "@/components/docs";
import { SetupVisual } from "@/components/feature-visuals";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/connectors-and-skills",
  "MCP connectors, skills and AGENTS.md",
  "Connect Wavefunction to 70+ ready-made MCP connectors or your own MCP server, and reuse your AGENTS.md instructions and SKILL.md skills.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Connectors and skills"
        lede="Connect the tools you work with, and reuse the instructions and skills you already have for other coding agents."
        updated="November 26, 2026"
      />

      <H2 id="connectors">Connectors</H2>
      <P>
        <UI>Connectors</UI> in the sidebar lists 70+ ready-made connectors, such as Gmail, Outlook,
        Microsoft 365, Airtable, Asana and Atlassian. Connect one, and the agent can use it in your
        chats.
      </P>
      <Figure
        src="/wavefunction-connectors.jpg"
        alt="The connector catalog in Wavefunction: Gmail, Outlook, Google, Microsoft 365, Airtable, Asana, Atlassian and more."
        width={2841}
        height={1855}
      />
      <P>To add any other MCP server, give it a name and either:</P>
      <List>
        <li>
          a command that starts it, such as <C>npx</C> with its arguments, an optional working
          directory and environment variables, or
        </li>
        <li>the address of a remote server, with an access token if it needs one.</li>
      </List>

      <Panel center>
        <SetupVisual />
      </Panel>

      <H2 id="agents-md">AGENTS.md</H2>
      <P>
        Wavefunction follows your project’s <C>AGENTS.md</C>, so instructions you wrote for other
        agents work here too.
      </P>

      <H2 id="skills">Skills</H2>
      <P>
        Wavefunction loads <C>SKILL.md</C> skills from these folders, in your projects and your home
        folder:
      </P>
      <List>
        <li>
          <C>.agents/skills</C>
        </li>
        <li>
          <C>.claude/skills</C>
        </li>
        <li>
          <C>.opencode/skills</C>
        </li>
      </List>
      <P>
        It also loads skills from <C>~/.codex/skills</C> and <C>~/.config/opencode/skills</C>.
      </P>

      <DocFooter path="/docs/connectors-and-skills" />
    </article>
  );
}
