import type { ReactNode } from "react";
import { Diagram, PostFooter, PostHeader, Stat } from "@/components/blog";
import { CacheDiagram, HelperStartChart, HelpersDiagram, HoldDiagram, SteeringDiagram } from "@/components/blog-diagrams";
import { A, C, Code, H2, H3, P, Table } from "@/components/docs";
import { Timeline } from "@/components/timeline";
import { POSTS, postMetadata } from "@/lib/blog";
import { site } from "@/lib/site";

const PATH = "/blog/parallel-execution";
const post = POSTS.find((entry) => entry.path === PATH)!;

export const metadata = postMetadata(PATH);

/** A file in the repository, linked on GitHub. */
function Src({ path, children }: { path: string; children?: ReactNode }) {
  return (
    <A href={`${site.github}/blob/main/${path}`}>
      <span className="font-mono text-[0.88em]">{children ?? path.split("/").pop()}</span>
    </A>
  );
}

function Pr({ number }: { number: number }) {
  return <A href={`${site.github}/pull/${number}`}>#{number}</A>;
}

function B({ children }: { children: ReactNode }) {
  return <strong className="font-semibold">{children}</strong>;
}

function Caption({ number, children }: { number: number; children: ReactNode }) {
  return (
    <>
      <b className="font-semibold text-soft">Figure {number}.</b> {children}
    </>
  );
}

const BACKEND = "backend/src/wavefunction";

export default function Page() {
  return (
    <article className="mx-auto max-w-[760px]">
      <PostHeader
        title={post.title}
        dek="What happens when you send a new task while the agent is still busy with the last one, the problems we ran into building it, and what we changed to make it fast."
        date={post.date}
        readTime={post.readTime}
      />
      <div className="mt-8 rounded-[24px] bg-panel p-3 sm:p-6">
        <Timeline />
      </div>

      <P>
        If you’ve used a coding agent for a while, you’ve probably had this happen: you give it a
        task, and while it’s working you think of the next thing you want. Most agents leave you two
        options at that point. You can interrupt the task that’s running, or you can keep the idea in
        your head until it finishes.
      </P>
      <P>
        In Wavefunction you can send the next message right away. The agent reads it at its next
        step, adjusts its current work if the message is about that, and if it’s a separate piece of
        work, starts a helper for it while it carries on with the original task. The animation above
        shows roughly what this looks like in the app.
      </P>
      <P>
        This post explains how that works under the hood, the problems we ran into while building
        it, and what we changed to make it fast. Throughout the post you’ll see references to
        implementation files in our open-source GitHub repository, in case you want to read the code.
      </P>

      <H2 id="helpers">Handing work to a helper</H2>
      <P>
        To work on two things at once, the agent first needs a way to pass a piece of work to someone
        else and keep going with its own. In Wavefunction that’s a tool called <C>spawn_agent</C>,
        which the agent calls itself. It lives in <Src path={`${BACKEND}/coding_agents.py`} />:
      </P>
      <Code>{`spawn_agent(task, title="Helper", read_only=False,
            isolated=False, model=None, files=None)`}</Code>
      <P>
        The call returns an id immediately, and the helper starts up as a new chat with its own
        conversation. It runs on the same model as the task that started it, with the same tools,
        skills and permissions. It doesn’t start with the parent’s conversation history, though, so
        the parent writes everything the helper needs to know into the <C>task</C> argument, which
        can be up to 30,000 characters long, and uses <C>files</C> for anything the helper should
        look at, such as a screenshot.
      </P>
      <P>
        Meanwhile, the parent carries on with its own share of the work. It can check on a helper
        with <C>get_agent</C> or wait for one or more of them with <C>wait_agents</C>, and whenever a
        helper finishes, the parent receives a short note about it before its next model call. Two
        rules keep this manageable: a helper can’t start helpers of its own, and the parent isn’t
        allowed to give its final answer while any of its helpers is still unresolved.
      </P>
      <Diagram
        caption={
          <Caption number={1}>
            This is the task from our live test: three pieces of work requested at once, followed by
            a fourth one sent while the agent is busy. The agent decides which helpers share the
            checkout and which ones get a worktree.
          </Caption>
        }
      >
        <HelpersDiagram />
      </Diagram>

      <H2 id="isolation">Keeping helpers off each other’s files</H2>
      <P>
        As soon as two helpers work in the same repository, they can overwrite each other’s changes,
        or one of them can build on a file that another is halfway through rewriting. We handle this
        in three ways, and the agent chooses one for each helper it starts.
      </P>
      <P>
        By default, a helper works in the parent’s own checkout and edits the same files on disk. To
        make that safe, the parent is told to give each helper files that nobody else is working on,
        and the engine runs the agent’s commands and file edits in a given folder one at a time,
        while letting reads through without waiting. If a helper tries to edit a file that has
        changed since it last read it, the edit is refused, and the error tells it which chat or
        helper made the change.
      </P>
      <P>
        For riskier work, such as a large refactor, the agent can pass <C>isolated=True</C>. The
        helper then gets its own Git worktree and branch, in a <C>.harness-worktrees</C> folder next
        to the repository. Before it starts, we take a snapshot of the parent’s checkout, including
        any uncommitted changes, and copy it into the worktree, so the helper begins from exactly the
        state the parent sees. When the helper is done, it hands back a diff between two commits, and
        the parent reviews it and applies it with the same Git and file tools it uses for everything
        else, much like reviewing a pull request.
      </P>
      <P>
        Finally, a helper that only needs to research something can be started as read-only. It gets
        just the tools that read, and it runs in Plan mode. A helper is also made read-only
        automatically if the task that started it can’t write.
      </P>

      <H2 id="concurrency">Making sure runs don’t collide</H2>
      <P>
        Running many tasks at once raises a more basic problem as well: two runs could try to write
        to the same chat, or change the same files, at the same moment. All of the runs live in a
        single engine process, where each one, whether it belongs to a chat or to a helper, is an{" "}
        <C>asyncio</C> task. A handful of locks decide what has to wait:
      </P>
      <Table
        head={["Lock", "What it keeps in order"]}
        rows={[
          ["Per chat", "Sending, compacting, forking, rewinding and starting helpers"],
          ["Per run", "A parent’s helper calls and replies, so two of them can’t race"],
          ["Per folder", "The agent’s commands and file edits, one at a time. Reads don’t take it"],
        ]}
      />
      <P>
        On top of that, each conversation has only one run writing to it at any time. If you send a
        message to a chat that’s busy, the message either steers the run that’s in progress or is
        stored in the database as a queued run, which starts once the current one ends. You can find
        that logic in <Src path={`${BACKEND}/runtime.py`} />.
      </P>

      <H2 id="steering">Getting your message to the right place</H2>
      <P>
        By default, a message you send while the agent is working steers the current task, and that
        raises two problems. The agent is usually in the middle of a model call, which a new message
        can’t be inserted into, and when helpers are running, the message may be about work one of
        them is doing rather than the parent.
      </P>
      <P>
        For the first problem, <Src path={`${BACKEND}/steering.py`} /> holds on to the message until
        the next natural break: just before the next model call, right after a final answer, or at
        the end of the run. We also save a checkpoint before the message is added, which means you
        can still rewind to the point just before you sent it.
      </P>
      <P>
        For the second, when the task receiving the message is a parent with helpers, we attach a
        short note to the message that reminds the agent to start a helper for anything new and
        independent in it, and to pass anything about work that’s already in progress to the helper
        responsible for it. You can’t type into a helper’s chat directly, so the parent does the
        forwarding with <C>send_message</C>. Depending on the helper’s state, that call either steers
        it while it’s running, answers a question it’s waiting on, or starts a follow-up run if it
        has already finished.
      </P>
      <P>
        If you’d rather not steer at all, tick <B>Queue follow-up</B> before sending, and the message
        waits until the current task has finished.
      </P>
      <Diagram
        caption={
          <Caption number={2}>
            In the live test, a correction to the billing rules arrives while a helper is building
            the billing module. The parent forwards it, and the helper continues from where it was.
          </Caption>
        }
      >
        <SteeringDiagram />
      </Diagram>

      <H2 id="cache">Keeping costs down with the prompt cache</H2>
      <P>
        Running helpers in parallel multiplies the number of conversations, and on every turn, each
        conversation sends the model its instructions, the definitions of its tools and its whole
        history so far. Most of that is the same as the turn before, so without help, you’d pay again
        and again for text the model has already seen, and the bill would grow with every helper you
        add.
      </P>
      <P>
        Providers offer a way around this. Most of them charge much less for the part of a request
        that matches the beginning of a recent request, but the match has to be exact: if a single
        byte changes near the start, everything after it is billed at full price. So the real
        problem is keeping the beginning of every request identical from one turn to the next.
      </P>
      <P>
        To do that, the system prompt stays the same for the whole conversation, and anything that
        changes over time, like the date, the environment or your <C>AGENTS.md</C>, is sent in
        messages instead: in full the first time, and then only as updates. Tools are always listed
        in sorted order, and if the agent discovers a new tool partway through a conversation, it’s
        added to the end of the list so that the tools before it stay where they are. Every request
        also includes the conversation’s id as its cache key, so the provider matches a helper’s
        requests against that helper’s own earlier requests. On top of that, requests to Anthropic
        models include cache breakpoints, and requests through OpenRouter include a session id.
      </P>
      <Diagram
        caption={
          <Caption number={3}>
            Only the end of each request is new. Everything before it has to match the previous
            request exactly for the provider to read it from the cache.
          </Caption>
        }
      >
        <CacheDiagram />
      </Diagram>

      <H2 id="problems">Problems we ran into</H2>
      <P>
        Measuring all of this closely, and running it many times, turned up three problems we had
        to fix.
      </P>

      <H3>Helpers were slow to start</H3>
      <P>
        Our first measurements showed a helper in the shared checkout taking about half a second to
        start, and one with its own worktree about one and a half seconds. That’s long enough that
        the agent would often be better off doing a small job itself, so we measured every step
        between the parent calling <C>spawn_agent</C> and the helper sending its first request to the
        model, and found three things that accounted for most of the time.
      </P>
      <P>
        The biggest was that the agent was rebuilt from scratch for every message. Each rebuild
        created 99 tools in order to use 46 of them, and LangChain generated a Pydantic model from
        every tool’s signature each time, which added about 280 ms to every message and every helper
        start. Tool groups are now built once per task, and the schemas are cached, keyed on each
        function’s code, annotations, default values and docstring. With those changes, building the
        agent went from about 370 ms to about 50 ms (<Pr number={29} />).
      </P>
      <P>
        The second was snapshots. Starting a helper in its own worktree used to run nine sandboxed
        processes, and three of them took a snapshot of the same checkout even though nothing in it
        had changed, each time running about 11 Git commands. A snapshot of an unchanged checkout now
        needs 5 Git commands, and a clean start runs three fewer processes (<Pr number={28} />).
      </P>
      <P>
        The third was the startup time of Python itself. Every sandboxed Python process imported the
        server’s modules before doing anything else, even though it didn’t need them. Moving those
        imports to the code that actually starts the server brought the start time down from 111 ms
        to 66 ms (<Pr number={27} />).
      </P>
      <div className="mt-8 grid gap-3 sm:grid-cols-2">
        <Stat label="Shared helper ready" before="454–498 ms" after="332–334 ms" note="From the spawn call to the helper’s first model call (#29)" />
        <Stat label="Isolated helper ready" before="1,555–1,590 ms" after="973–998 ms" note="Median of 11 starts, two rounds (#28)" />
        <Stat label="Agent build" before="~370 ms" after="~50 ms" note="Measured under the profiler (#29)" />
        <Stat label="Send to model request" before="471–484 ms" after="268 ms" note="Same chat, until the request leaves for the model (#29)" />
      </div>

      <H3>Checkpoints grew with every step</H3>
      <P>
        Every chat and every helper keeps checkpoints, which is what makes rewinding and forking
        possible. They used to store a complete copy of the conversation at every step, so the longer
        a chat got, the more each new step wrote, and by the 60th message a single message was
        writing 16.5 MB to disk. With several helpers each keeping their own checkpoints, that adds
        up quickly.
      </P>
      <P>
        Checkpoints now keep each message once per conversation. The 60th message writes 128 KB, and
        a chat of 60 exchanges takes 8.4 MB of storage instead of 37.7 MB (<Pr number={31} />).
      </P>
      <div className="mt-8 grid gap-3 sm:grid-cols-2">
        <Stat label="Written by message 60" before="16.5 MB" after="128 KB" note="One chat on a synthetic repository (#31)" />
        <Stat label="Storage after 60 exchanges" before="37.7 MB" after="8.4 MB" note="Including the code history (#31)" />
      </div>

      <H3>Some helpers got stuck repeating an edit</H3>
      <P>
        The third problem only showed up after we had run the live test many times. Every so often a
        helper would send the same failing <C>edit_file</C> call over and over until the run was
        stopped, often after the actual work was already done, and when this happened to a parent, it
        never reported back at all.
      </P>
      <P>
        It turned out to depend on which provider served the model. One provider ran DeepSeek V4 Pro
        without reasoning unless it was explicitly requested, and in 910 calls we saw no reasoning
        tokens at all, whereas on two other providers about 90% of calls included reasoning. That
        difference mattered a lot: after an edit failed, the model without reasoning sent exactly the
        same edit again about 80% of the time, compared with 35 to 49% when reasoning was on.
      </P>
      <P>
        We tried turning reasoning on by default, but it doubled the cost and the run time without
        getting more runs to pass, and clearer error messages didn’t help either. What finally worked
        was taking the repeated tool away for a while, which is what{" "}
        <Src path={`${BACKEND}/repeated_actions.py`}>RepeatHoldMiddleware</Src> does. It keeps track
        of the most recent action that did more than read, and if that action returns the same result
        three times in a row, the next request is sent without that tool, together with a note
        explaining why it’s missing. As soon as the model does something different, the tool becomes
        available again.
      </P>
      <Diagram
        caption={
          <Caption number={4}>
            Reading the file again doesn’t lift the hold, because reads are never held, but writing it
            does.
          </Caption>
        }
      >
        <HoldDiagram />
      </Diagram>
      <P>The table below shows the effect on the live test:</P>
      <Table
        head={["Provider routing", "Build", "Runs passed", "Helpers stuck", "Parents stuck"]}
        rows={[
          ["One provider", "Before", "6 of 10", "3 of 40", "4"],
          ["One provider", "With the hold", "14 of 15", "1 of 56", "0"],
          ["Default", "Before", "8 of 9", "1 of 36", "1"],
          ["Default", "With the hold", "14 of 15", "0 of 56", "0"],
        ]}
      />
      <P>
        Across both setups, 28 of the 30 runs passed with the hold in place, and only 1 of 112
        helpers got stuck. Neither of the two failed runs involved a loop: in both cases the parent
        decided to do all the work itself and never started a helper. The hold also had very little
        effect on caching. It held 25 of 540 calls, and the share of input read from the cache went
        from 84.6% to 82.5% (<Pr number={34} />).
      </P>

      <H2 id="measuring">How we measured</H2>
      <P>
        The start-up times were measured without a real model. A scripted model from our backend test
        suite answers instantly, working against a synthetic Git repository with 20 files and a
        throwaway data folder. Because the machine wasn’t idle during the measurements, we alternated
        between the old and new builds so that any background load affected both equally, and each
        figure is the median of 11 starts.
      </P>
      <P>
        The live results come from <Src path="scripts/verify-orchestration-live.py" />, which you can
        run yourself with an OpenRouter key. It gives a real model a synthetic invoice repository and
        asks for three pieces of work to be done in parallel, without ever mentioning helpers.
        Partway through the run, it asks for a fourth module and sends a correction for one of the
        parts that’s being worked on. At the end, it checks 12 things, including that helpers were
        started, that both the extra request and the correction arrived, that the correct helper
        applied the correction, that the results were combined, that every helper finished, and that
        a simple request was handled without delegating. Each run costs about 20 cents.
      </P>

      <H2 id="compare">How we compare</H2>
      <P>
        To see how far we still have to go, we also ran three popular terminal coding agents through
        the same measurement, on the same Mac and on the same day as our first baseline. No real
        model was involved: a local stub answered every request, and we timed the interval from the
        helper call to the helper’s first model request. We tried a fourth agent as well, but it
        doesn’t have built-in helpers.
      </P>
      <P>
        On this measurement Wavefunction is still the slowest. The other agents start a helper in 44
        to 144 ms, while ours takes about 330 ms. Only one of them can give a helper its own
        worktree, which takes 182 ms, compared with about a second for ours. That second is an
        improvement on the second and a half it used to take, but it’s still a long way off, and most
        of it goes to sandboxed processes and to the Git snapshots that rewind depends on.
      </P>
      <Diagram
        caption={
          <Caption number={5}>
            The time from the helper call to the helper’s first model request, measured without a
            real model. The other agents were measured on October 31 and may be faster or slower
            today. The Wavefunction figure is the slower of the two rounds we measured after #28 and
            #29.
          </Caption>
        }
      >
        <HelperStartChart />
      </Diagram>

      <H2 id="next">What’s next</H2>
      <P>
        The clearest next step is helper start time, particularly for helpers with their own
        worktree. After that, we want the parent to delegate more reliably, since it still sometimes
        does all the work itself, and that’s exactly what caused the two failed runs above.
      </P>
      <P>
        We also haven’t yet measured the thing this post is really about, which is how long several
        tasks take when you send them to one chat, compared with running them one after another.
        That’s the next measurement we plan to make.
      </P>

      <PostFooter />
    </article>
  );
}
