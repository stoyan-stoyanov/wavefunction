import { open } from "@tauri-apps/plugin-dialog";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { RunConnectorSetup } from "@/components/run-connector-setup";
import {
  api,
  cancelCatalogConnection,
  catalogConnectionStatus,
  connectCatalogEntry,
  gitHubSignInStatus,
  inspectLocalIntegration,
  listConnectorCatalog,
  openExternal,
  startGitHubSignIn,
} from "@/lib/api";
import type { Grant, GrantState, RunConnector, RunEvent } from "@/lib/types";
import { useSettings } from "@/store/settings";

vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  listConnectorCatalog: vi.fn(),
  connectCatalogEntry: vi.fn(),
  cancelCatalogConnection: vi.fn(),
  catalogConnectionStatus: vi.fn(),
  getCatalogSetup: vi.fn(),
  inspectLocalIntegration: vi.fn(),
  installLocalIntegration: vi.fn(),
  startGitHubSignIn: vi.fn(),
  gitHubSignInStatus: vi.fn(),
  openExternal: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));

const notion: RunConnector = {
  id: "notion",
  title: "Notion",
  catalog_id: "notion",
  auth: "oauth",
  state: "sign_in",
  tool_count: 0,
  signing_in: false,
  error: "",
};
const vercel: RunConnector = {
  ...notion,
  id: "vercel",
  title: "Vercel",
  catalog_id: "vercel",
  state: "ready",
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  useSettings.setState({
    ...useSettings.getInitialState(),
    connectors: [],
    loadConnectors: vi.fn().mockResolvedValue(undefined),
  });
  vi.spyOn(api, "runConnectorSetup").mockResolvedValue({
    status: "waiting_connector",
    project_id: "project-1",
    connectors: [vercel, notion],
  });
  vi.mocked(listConnectorCatalog).mockResolvedValue([
    {
      id: "notion",
      title: "Notion",
      description: "Pages",
      url: "https://mcp.notion.com/mcp",
      auth: "oauth",
      transport: "http",
      docs: "https://notion.com",
      notes: "",
      local: false,
    },
  ]);
  vi.mocked(connectCatalogEntry).mockResolvedValue({
    name: "notion",
    auth: "oauth",
  });
  vi.mocked(cancelCatalogConnection).mockResolvedValue({ cancelled: true });
  vi.mocked(catalogConnectionStatus).mockResolvedValue({});
});

const paused: RunEvent = {
  node_id: null,
  id: "setup-1",
  run_id: "run-1",
  sequence: 1,
  event_type: "run_connector_setup_required",
  payload: { connectors: [vercel, notion] },
  created_at: "2026-09-13T00:00:00Z",
};

it("signs gh in from the paused conversation and tells the task, which then resumes", async () => {
  const github: RunConnector = {
    id: "github",
    title: "GitHub",
    catalog_id: null,
    local_kind: "github",
    auth: "none",
    state: "setup",
    error: "",
    tool_count: 0,
    signing_in: false,
  };
  vi.mocked(api.runConnectorSetup).mockResolvedValue({
    status: "waiting_connector",
    project_id: "project-1",
    connectors: [github],
  });
  const signedIn = vi.spyOn(api, "githubSignedIn").mockResolvedValue({ account: "synthetic-user" });
  vi.mocked(inspectLocalIntegration).mockResolvedValue({
    installed: true,
    version: "test",
    accounts: [],
    error: "",
  });
  vi.mocked(startGitHubSignIn).mockResolvedValue({
    running: true,
    code: "TEST-CODE",
    account: "",
    error: "",
  });
  vi.mocked(gitHubSignInStatus).mockResolvedValue({
    running: false,
    code: "",
    account: "synthetic-user",
    error: "",
  });
  vi.mocked(openExternal).mockResolvedValue(undefined);
  render(
    <RunConnectorSetup
      runId="github-run"
      events={[
        {
          ...paused,
          payload: { connectors: [github] },
        },
      ]}
    />,
  );
  expect(await screen.findByLabelText("One-time code")).toHaveTextContent("TEST-CODE");
  fireEvent.click(screen.getByRole("button", { name: "Open GitHub" }));
  expect(openExternal).toHaveBeenCalledWith("https://github.com/login/device");
  await waitFor(() => expect(signedIn).toHaveBeenCalledExactlyOnceWith("github-run"), { timeout: 2000 });
  vi.mocked(api.runConnectorSetup).mockResolvedValue({
    status: "running",
    project_id: "project-1",
    connectors: [],
  });
  await waitFor(() => expect(screen.queryByText("GitHub")).not.toBeInTheDocument(), {
    timeout: 2000,
  });
  expect(api.runConnectorSetup).toHaveBeenLastCalledWith("github-run");
});

const setting = (
  id: string,
  title: string,
  local_kind: RunConnector["local_kind"],
  extra: Partial<RunConnector> = {},
): RunConnector => ({
  id,
  title,
  catalog_id: null,
  local_kind,
  auth: "none",
  state: "setup",
  tool_count: 0,
  signing_in: false,
  error: "",
  ...extra,
});

const grantState = (grants: Grant[]): GrantState => ({
  grants,
  sandbox_ok: true,
  sandbox_problem: null,
});

it("allows the folder an agent's task needs, without making it the folder the agent works in", async () => {
  // An agent saving a file from its desktop to the Mac names the folder it needs.
  const downloads = setting("folder-access", "Folder access", "folder", {
    path: "/Users/me/Downloads",
    writable: true,
  });
  vi.mocked(api.runConnectorSetup).mockResolvedValue({
    status: "waiting_connector",
    project_id: "project-1",
    connectors: [downloads],
  });
  vi.mocked(open).mockResolvedValue("/Users/me/Downloads");
  vi.spyOn(api, "listGrants").mockResolvedValue(grantState([]));
  const grant = vi.spyOn(api, "addGrant").mockResolvedValue({
    grants: [{ path: "/Users/me/Downloads", writable: true }],
  });
  const choose = vi.spyOn(api, "chooseRunFolder").mockResolvedValue({ chosen: true });
  render(<RunConnectorSetup runId="run-1" events={[{ ...paused, payload: { connectors: [downloads] } }]} />);
  expect(screen.getByText(/The agent asked for access to a folder/)).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("Read and write access to /Users/me/Downloads");
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() => expect(grant).toHaveBeenCalledWith("/Users/me/Downloads", true));
  expect(open).toHaveBeenCalledWith({
    directory: true,
    multiple: false,
    defaultPath: "/Users/me/Downloads",
  });
  // The task carries on once the folder is allowed; it is not told to work there.
  await waitFor(() => expect(screen.getByLabelText("Checking Folder access")).toBeInTheDocument());
  expect(choose).not.toHaveBeenCalled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("tells a task which folder was picked, even another than the one it named", async () => {
  // An agent saving a file from its desktop to the Mac named ~/Downloads, and the user picked
  // another folder: waiting for the named one left the task waiting for ever.
  const downloads = setting("folder-access", "Folder access", "folder", {
    path: "/Users/me/Downloads",
    writable: true,
  });
  vi.mocked(api.runConnectorSetup).mockResolvedValue({
    status: "waiting_connector",
    project_id: "project-1",
    connectors: [downloads],
  });
  vi.mocked(open).mockResolvedValue("/Users/me/Documents/Health");
  vi.spyOn(api, "listGrants").mockResolvedValue(grantState([]));
  const grant = vi.spyOn(api, "addGrant").mockResolvedValue({
    grants: [{ path: "/Users/me/Documents/Health", writable: true }],
  });
  const choose = vi.spyOn(api, "chooseRunFolder").mockResolvedValue({ chosen: true });
  render(<RunConnectorSetup runId="run-1" events={[{ ...paused, payload: { connectors: [downloads] } }]} />);
  expect(screen.getByText(/The agent asked for access to a folder/)).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("Read and write access to /Users/me/Downloads");
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  // Named by the card's row, so the task never takes it for the folder the agent works in.
  await waitFor(() =>
    expect(choose).toHaveBeenCalledWith("run-1", "/Users/me/Documents/Health", "folder-access"),
  );
  expect(open).toHaveBeenCalledWith({
    directory: true,
    multiple: false,
    defaultPath: "/Users/me/Downloads",
  });
  expect(grant).toHaveBeenCalledWith("/Users/me/Documents/Health", true);
  await waitFor(() => expect(screen.getByLabelText("Checking Folder access")).toBeInTheDocument());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps folder setup paused when saving access fails and allows retry", async () => {
  const row = setting("folder-access", "Folder access", "folder", {
    path: "/Users/me/Downloads",
    writable: true,
  });
  vi.mocked(api.runConnectorSetup).mockResolvedValue({
    status: "waiting_connector",
    project_id: "project-1",
    connectors: [row],
  });
  vi.mocked(open).mockResolvedValue("/Users/me/Documents/Health");
  vi.spyOn(api, "listGrants").mockResolvedValue(grantState([]));
  const grant = vi
    .spyOn(api, "addGrant")
    .mockRejectedValueOnce(new Error("Folder access could not be saved"))
    .mockResolvedValue({ grants: [{ path: "/Users/me/Documents/Health", writable: true }] });
  const choose = vi.spyOn(api, "chooseRunFolder").mockResolvedValue({ chosen: true });
  render(<RunConnectorSetup runId="run-1" events={[{ ...paused, payload: { connectors: [row] } }]} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Folder access could not be saved");
  expect(choose).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Checking Folder access")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));

  await waitFor(() =>
    expect(choose).toHaveBeenCalledWith("run-1", "/Users/me/Documents/Health", "folder-access"),
  );
  expect(grant).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("offers Settings' Docker actions on the paused task: check, confirmed restart, start, then build", async () => {
  const stopped: RunConnector = {
    id: "computer-use",
    title: "Computer use",
    catalog_id: null,
    local_kind: "computer",
    auth: "none",
    state: "error",
    tool_count: 0,
    signing_in: false,
    error:
      "Docker is installed but its engine is not running. Start Docker Desktop (or OrbStack) and try again.",
    build_log: [],
    docker_startable: true,
  };
  vi.mocked(api.runConnectorSetup).mockResolvedValue({
    status: "waiting_connector",
    project_id: "project-1",
    connectors: [stopped],
  });
  const probe = vi.spyOn(api, "computerStatus").mockResolvedValue({
    docker_available: true,
    docker_desktop_available: true,
    image_ready: false,
    docker_problem: "Docker is installed but its engine is not running.",
  } as never);
  const start = vi.spyOn(api, "manageDocker").mockResolvedValue({
    docker_available: true,
    docker_desktop_available: true,
    image_ready: false,
    docker_problem: null,
  } as never);
  const build = vi.spyOn(api, "buildComputerImage").mockResolvedValue({} as never);
  render(<RunConnectorSetup runId="run-1" events={[{ ...paused, payload: { connectors: [stopped] } }]} />);
  expect(screen.getByText("Setting up computer use")).toBeInTheDocument();
  expect(screen.getByText("This task carries on by itself once the desktop is ready.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel this task" })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Setup needed");
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() => expect(probe).toHaveBeenCalledExactlyOnceWith(true));
  expect(start).not.toHaveBeenCalled();

  // A restart interrupts every container, so it asks first, as in Settings.
  fireEvent.click(screen.getByRole("button", { name: "Restart Docker" }));
  const confirm = screen.getByRole("alertdialog");
  expect(confirm).toHaveTextContent("interrupts all running Docker containers");
  fireEvent.click(within(confirm).getByRole("button", { name: "Restart Docker" }));
  await waitFor(() => expect(build).toHaveBeenCalledOnce());
  expect(start).toHaveBeenLastCalledWith("restart");

  // The next poll still finds Docker stopped.
  fireEvent.click(await screen.findByRole("button", { name: "Start Docker" }, { timeout: 3000 }));
  await waitFor(() => expect(build).toHaveBeenCalledTimes(2));
  expect(start).toHaveBeenLastCalledWith("start");
});

it("retries a task connector failure using the request's ID", async () => {
  const failed = {
    ...notion,
    state: "error" as const,
    error: "Connection timed out",
  };
  vi.mocked(api.runConnectorSetup).mockResolvedValue({
    status: "waiting_connector",
    project_id: "project-1",
    connectors: [failed],
  });
  const refresh = vi
    .spyOn(api, "refreshConnector")
    .mockResolvedValue({ connected: false, error: "Connection timed out", tools: [] });
  render(<RunConnectorSetup runId="run-2" events={[{ ...paused, payload: { connectors: [failed] } }]} />);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(refresh).toHaveBeenCalledWith("notion"));
  await waitFor(() => expect(api.runConnectorSetup).toHaveBeenCalledTimes(2));
  expect(api.runConnectorSetup).toHaveBeenLastCalledWith("run-2");
  expect(connectCatalogEntry).not.toHaveBeenCalled();
  expect(screen.queryByText("Sign-in required")).not.toBeInTheDocument();
});
