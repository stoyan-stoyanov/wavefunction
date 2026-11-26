import { DocFooter, DocHeader, H2, List, Note, P, Panel, Table, UI } from "@/components/docs";
import { RoutinesVisual } from "@/components/feature-visuals";
import { docMetadata } from "@/lib/docs";

export const metadata = docMetadata(
  "/docs/routines",
  "Schedule coding agent tasks with routines",
  "Run a coding agent task on a schedule with Wavefunction routines: every few minutes, daily, on chosen weekdays, monthly or once, in a chat that remembers earlier runs.",
);

export default function Page() {
  return (
    <article>
      <DocHeader
        title="Scheduled tasks"
        lede="A routine is a chat that gets the same instruction on a schedule. Every run lands in the same chat, so it knows what earlier runs found."
        updated="November 26, 2026"
      />
      <Panel center>
        <RoutinesVisual />
      </Panel>

      <H2 id="create">Create a routine</H2>
      <P>
        Ask any chat in a project, in your own words. For example: “Every weekday at 9, review
        yesterday’s merged pull requests and list anything that needs a follow-up.”
      </P>
      <P>
        The agent creates the routine as a new chat in the same project, with the model and access of
        the chat you asked. A clock icon marks it in the sidebar.
      </P>

      <H2 id="schedules">Schedules</H2>
      <Table
        head={["Repeats", "Example"]}
        rows={[
          ["Every few minutes, hours, days or weeks", "Every 2 hours. The shortest is every minute."],
          ["Daily", "Every day at 09:00"],
          ["On chosen days of the week", "On weekdays at 09:00, or on Mon and Thu at 14:00"],
          ["Monthly", "On the 1st of each month at 10:00. The 31st means the last day of shorter months."],
          ["Once", "On a date and time you choose"],
        ]}
      />
      <P>Times follow your computer’s clock, including daylight-saving changes.</P>

      <H2 id="settings">Change a routine</H2>
      <P>
        Open the chat’s menu in the sidebar and choose <UI>Routine settings</UI> to change the{" "}
        <UI>Instruction</UI>, the <UI>Schedule</UI>, or where it reports with{" "}
        <UI>Report each run to</UI>. A routine can report each finished run to another chat, which
        picks it up from there.
      </P>
      <List>
        <li>Archiving the routine’s chat pauses it.</li>
        <li>Deleting the chat deletes the routine.</li>
      </List>

      <H2 id="when-it-runs">When it runs</H2>
      <List>
        <li>Routines run while Wavefunction is open.</li>
        <li>A run missed while the app was closed happens once when you open it, not once per missed time.</li>
        <li>If the routine’s chat is busy, the run waits until it’s free.</li>
      </List>
      <Note>Routines belong to a project, so ask from a chat that’s in one.</Note>

      <DocFooter path="/docs/routines" />
    </article>
  );
}
