import { NativeSection, NativeView, ViewHeading } from "@/components/native-view";
import { RestartDockerDialog } from "@/components/restart-docker-dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { ComputerStatus } from "@/lib/types";
import { Info, LoaderCircle, RotateCcw, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

export function ComputerSettings() {
  const [status, setStatus] = useState<ComputerStatus | null>(null);
  const [probing, setProbing] = useState(false);
  const [recovering, setRecovering] = useState<"start" | "restart" | null>(null);
  const [confirmRestart, setConfirmRestart] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const dockerAction = recovering ?? status?.docker_action;
  const busy = probing || Boolean(dockerAction) || Boolean(status?.building);

  async function recover(action: "start" | "restart") {
    setRecovering(action);
    setRecoveryError(null);
    try {
      setStatus(await api.manageDocker(action));
    } catch (error) {
      setRecoveryError(
        error instanceof Error
          ? error.message
          : "Could not recover Docker. Open Docker Desktop and try again.",
      );
      try {
        setStatus(await api.computerStatus(true));
      } catch {
        /* Keep the recovery error visible. */
      }
    } finally {
      setRecovering(null);
    }
  }

  async function probe() {
    setProbing(true);
    try {
      setStatus(await api.computerStatus(true));
    } catch {
      toast.error("Could not check Docker.");
    } finally {
      setProbing(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    api
      .computerStatus(true)
      .then((current) => {
        if (!cancelled) setStatus(current);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // While a build streams, poll the cached status so the log grows without re-probing Docker.
  useEffect(() => {
    if (!status?.building && !status?.docker_action) return;
    const timer = setInterval(() => {
      api
        .computerStatus()
        .then(setStatus)
        .catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, [status?.building, status?.docker_action]);

  async function build() {
    try {
      await api.buildComputerImage();
      setStatus((current) => (current ? { ...current, building: true } : current));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start the build.");
    }
  }

  return (
    <NativeView>
      <ViewHeading title="Computer use" />
      <NativeSection
        title="Docker"
        description="The desktop runs inside Docker, which is installed separately and stays under your control."
      >
        {status && !status.docker_available ? (
          <div className="flex items-start gap-2 rounded-xl bg-muted/60 px-4 py-3 text-sm text-muted-foreground">
            <Info className="mt-0.5 size-4 shrink-0" />
            <span>
              Docker is not installed. Download Docker Desktop from{" "}
              <span className="font-medium">docker.com/products/docker-desktop</span>, open it once, then come
              back here.
            </span>
          </div>
        ) : null}

        {status?.docker_problem ? (
          <div className="flex items-start gap-2 rounded-xl bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" />
            <span>{status.docker_problem}</span>
          </div>
        ) : null}

        {status?.docker_available && !status.docker_problem && !dockerAction ? (
          <p className="rounded-xl bg-card px-4 py-3 text-sm">Docker is installed and running.</p>
        ) : null}

        {dockerAction ? (
          <p role="status" className="mt-3 flex items-center gap-2 text-sm">
            <LoaderCircle className="size-4 animate-spin" />
            {dockerAction === "start" ? "Starting Docker…" : "Restarting Docker…"}
          </p>
        ) : null}
        {recoveryError ? (
          <p role="alert" className="mt-3 text-sm text-destructive">
            {recoveryError}
          </p>
        ) : null}
        <div className="mt-4 flex flex-wrap gap-2">
          {status?.docker_desktop_available ? (
            <>
              {status.docker_problem ? (
                <Button size="sm" disabled={busy} onClick={() => void recover("start")}>
                  Start Docker
                </Button>
              ) : null}
              <Button variant="secondary" size="sm" disabled={busy} onClick={() => setConfirmRestart(true)}>
                <RotateCcw /> Restart Docker
              </Button>
            </>
          ) : null}
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => void probe()}>
            <RotateCcw /> Check again
          </Button>
        </div>
        <RestartDockerDialog
          open={confirmRestart}
          onOpenChange={setConfirmRestart}
          onRestart={() => void recover("restart")}
        />
      </NativeSection>

      <NativeSection
        title="Desktop image"
        description="Built on this Mac from files that ship with the app. Some app updates require a desktop update. Building can download a few hundred megabytes and take a few minutes."
      >
        {status?.image_ready && !status.building ? (
          <p className="rounded-xl bg-card px-4 py-3 text-sm">
            The desktop image is built and ready. Chats can use computer tools now.
          </p>
        ) : null}

        {/* With the engine down the images can't be listed, so "not built" would be a guess. */}
        {status?.docker_problem && !status.building ? (
          <p className="rounded-xl bg-card px-4 py-3 text-sm text-muted-foreground">
            Start Docker to check the desktop image.
          </p>
        ) : null}

        {status && !status.image_ready && !status.building && !status.docker_problem ? (
          <div className="flex items-center justify-between gap-3 rounded-xl bg-card px-4 py-3">
            <p className="text-sm text-muted-foreground">
              {status.image_update_available
                ? "Desktop update available. Update it to use computer tools with this version of Wavefunction."
                : "The desktop image is not built yet."}
            </p>
            <Button size="sm" disabled={busy || !status.docker_available} onClick={() => void build()}>
              {status.image_update_available ? "Update desktop" : "Build the desktop"}
            </Button>
          </div>
        ) : null}

        {status?.building ? (
          <div className="rounded-xl bg-card px-4 py-3">
            <p className="text-sm">
              {status.image_update_available ? "Updating the desktop…" : "Building the desktop image…"}
            </p>
            <pre className="mt-2 max-h-44 overflow-auto font-mono text-2xs leading-4 text-muted-foreground">
              {status.build_log.slice(-15).join("\n") || "Starting…"}
            </pre>
          </div>
        ) : null}

        <p className="mt-4 text-xs leading-5 text-muted-foreground">
          The desktop can reach the internet, so treat what the agent does there like any web browsing. The
          desktop is thrown away after twenty idle minutes. Computer use needs a model that can see images.
        </p>
      </NativeSection>
    </NativeView>
  );
}
