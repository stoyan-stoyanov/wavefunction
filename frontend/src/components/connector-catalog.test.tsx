import { ConnectorCatalog } from "@/components/connector-catalog";
import {
  catalogConnectionStatus,
  connectCatalogEntry,
  getCatalogSetup,
  listConnectorCatalog,
  openExternal,
  type CatalogEntry,
} from "@/lib/api";
import { useSettings } from "@/store/settings";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  listConnectorCatalog: vi.fn(),
  getCatalogSetup: vi.fn(),
  connectCatalogEntry: vi.fn(),
  catalogConnectionStatus: vi.fn(),
  cancelCatalogConnection: vi.fn(),
  openExternal: vi.fn(),
}));
const entries: CatalogEntry[] = [
  {
    id: "agentcouch",
    title: "AgentCouch",
    description: "Shared messaging rooms",
    url: "https://mcp.agentcouch.dev/",
    auth: "oauth",
    transport: "http",
    docs: "https://agentcouch.dev",
    notes: "",
    local: false,
  },
  {
    id: "notion",
    title: "Notion",
    description: "Pages and databases",
    url: "https://mcp.notion.com/mcp",
    auth: "oauth",
    transport: "http",
    docs: "https://notion.com",
    notes: "",
    local: false,
  },
  {
    id: "deepwiki",
    title: "DeepWiki",
    description: "Repository documentation",
    url: "https://mcp.deepwiki.com/mcp",
    auth: "none",
    transport: "http",
    docs: "https://deepwiki.com",
    notes: "",
    local: false,
  },
];
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listConnectorCatalog).mockResolvedValue(entries);
  vi.mocked(catalogConnectionStatus).mockResolvedValue({});
  vi.mocked(connectCatalogEntry).mockResolvedValue({
    name: "notion",
    auth: "oauth",
  });
  useSettings.setState({
    connectors: [],
    loadConnectors: vi.fn().mockResolvedValue(undefined),
  });
});
it("starts browser sign-in from a predefined connector and shows pending state", async () => {
  render(<ConnectorCatalog onOpen={vi.fn()} />);
  fireEvent.click(await screen.findByText("Notion"));
  fireEvent.click(screen.getByRole("button", { name: "Connect Notion" }));
  await waitFor(() => expect(connectCatalogEntry).toHaveBeenCalledWith("notion", {}));
  expect(await screen.findByRole("button", { name: "Cancel connection" })).toBeInTheDocument();
  expect(screen.queryByText("Connected. Open the connector to review its tools.")).not.toBeInTheDocument();
});
it("keeps connection failures visible and allows retry", async () => {
  vi.mocked(catalogConnectionStatus).mockResolvedValue({
    notion: { state: "failed", error: "Provider refused client registration" },
  });
  render(<ConnectorCatalog onOpen={vi.fn()} />);
  fireEvent.click(await screen.findByText("Notion"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Provider refused");
  expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
});

it("shows Snowflake setup instructions, restores configuration, and submits OAuth settings", async () => {
  vi.mocked(listConnectorCatalog).mockResolvedValue([
    {
      id: "snowflake",
      title: "Snowflake",
      description: "Data tools",
      url: "",
      auth: "oauth",
      transport: "http",
      docs: "https://docs.snowflake.com",
      notes: "Create an OAuth security integration using the callback URL below.",
      local: false,
      setup: "registered_oauth",
    },
  ]);
  vi.mocked(getCatalogSetup).mockResolvedValue({
    url: "https://account.snowflakecomputing.com/api/v2/databases/DB/schemas/S/mcp-servers/M",
    client_id: "saved-client",
    secret_saved: "true",
    redirect_uri: "http://127.0.0.1:43827/callback",
    scope: "",
    auth_method: "client_secret_basic",
  });
  render(<ConnectorCatalog onOpen={vi.fn()} />);
  fireEvent.click(await screen.findByText("Snowflake"));
  await waitFor(() => expect(screen.getByLabelText("OAuth client ID")).toHaveValue("saved-client"));
  // The app's own window ignores a new-window link: the guide opens in the browser.
  vi.mocked(openExternal).mockResolvedValue();
  fireEvent.click(screen.getByRole("link", { name: /Snowflake setup guide/ }));
  expect(openExternal).toHaveBeenCalledWith("https://docs.snowflake.com");
  expect(screen.getByLabelText("OAuth client secret")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Connect Snowflake" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("OAuth scopes (optional)"), {
    target: { value: "session:role:ANALYST" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connect Snowflake" }));
  await waitFor(() =>
    expect(connectCatalogEntry).toHaveBeenCalledWith(
      "snowflake",
      expect.objectContaining({
        client_id: "saved-client",
        scope: "session:role:ANALYST",
        redirect_uri: "http://127.0.0.1:43827/callback",
      }),
    ),
  );
});

it("requires a replacement secret when changing the saved client", async () => {
  vi.mocked(listConnectorCatalog).mockResolvedValue([
    {
      id: "snowflake",
      title: "Snowflake",
      description: "Data",
      url: "",
      auth: "oauth",
      transport: "http",
      docs: "https://docs.snowflake.com",
      notes: "Setup",
      local: false,
      setup: "registered_oauth",
    },
  ]);
  vi.mocked(getCatalogSetup).mockResolvedValue({
    url: "https://account.snowflakecomputing.com/api/v2/databases/DB/schemas/S/mcp-servers/M",
    client_id: "saved-client",
    scope: "",
    auth_method: "client_secret_basic",
    secret_saved: "true",
    redirect_uri: "http://127.0.0.1:43827/callback",
  });
  render(<ConnectorCatalog onOpen={vi.fn()} />);
  fireEvent.click(await screen.findByText("Snowflake"));
  await waitFor(() => expect(screen.getByLabelText("OAuth client ID")).toHaveValue("saved-client"));
  fireEvent.change(screen.getByLabelText("OAuth client ID"), {
    target: { value: "new-client" },
  });
  expect(screen.getByRole("button", { name: "Connect Snowflake" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("OAuth client secret"), {
    target: { value: "replacement" },
  });
  expect(screen.getByRole("button", { name: "Connect Snowflake" })).toBeEnabled();
});

it("connects Render using its API key setup", async () => {
  vi.mocked(listConnectorCatalog).mockResolvedValue([
    {
      id: "render",
      title: "Render",
      description: "Deployments",
      url: "https://mcp.render.com/mcp",
      auth: "api_key",
      transport: "http",
      docs: "https://render.com/docs/mcp-server",
      notes: "Create a Render API key in Account Settings.",
      local: false,
      setup: "api_key",
    },
  ]);
  vi.mocked(getCatalogSetup).mockResolvedValue({
    url: "https://mcp.render.com/mcp",
    redirect_uri: "http://127.0.0.1:43827/callback",
    client_id: "",
    scope: "",
    auth_method: "client_secret_basic",
    secret_saved: "false",
  });
  render(<ConnectorCatalog onOpen={vi.fn()} />);
  fireEvent.click(await screen.findByText("Render"));
  await waitFor(() => expect(screen.queryByText("Loading saved setup…")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Render API key"), {
    target: { value: "render-fixture" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() =>
    expect(connectCatalogEntry).toHaveBeenCalledWith(
      "render",
      expect.objectContaining({ api_key: "render-fixture" }),
    ),
  );
});
