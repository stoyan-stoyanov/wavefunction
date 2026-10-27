import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

/** Asks before a Docker restart, which interrupts every running container. */
export function RestartDockerDialog({
  open,
  onOpenChange,
  onRestart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRestart: () => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Restart Docker?</AlertDialogTitle>
          <AlertDialogDescription>
            This interrupts all running Docker containers, including agent desktops and containers used by
            other apps. Wait for important work to finish first. If Docker Desktop is not responding, it is
            force quit and opened again.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onRestart}>Restart Docker</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
