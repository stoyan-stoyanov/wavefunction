/** Stands in for a conversation while its history loads. */
export function ChatSkeleton({
  label = "Loading conversation",
  composer = true,
}: {
  label?: string;
  composer?: boolean;
}) {
  return (
    <div
      className="chat-skeleton flex min-h-0 flex-1 overflow-hidden"
      role="status"
      aria-label={label}
      aria-busy="true"
    >
      <span className="sr-only">{label}…</span>
      <div
        aria-hidden="true"
        className="mx-auto flex w-full max-w-[780px] flex-col gap-8 px-6 py-9 sm:px-[38px] motion-safe:animate-pulse"
      >
        <div className="flex min-h-0 flex-1 flex-col gap-8 overflow-hidden">
          {["w-2/5", "w-3/5", "w-1/3"].map((width) => (
            <div className="flex shrink-0 flex-col gap-6" key={width}>
              <div className={`h-12 ${width} self-end rounded-2xl bg-muted`} />
              <div className="space-y-3">
                <div className="h-3 w-4/5 rounded bg-muted" />
                <div className="h-3 w-full rounded bg-muted" />
                <div className="h-3 w-3/5 rounded bg-muted" />
              </div>
            </div>
          ))}
        </div>
        {composer ? <div className="h-28 shrink-0 rounded-2xl border border-border bg-muted/40" /> : null}
      </div>
    </div>
  );
}
