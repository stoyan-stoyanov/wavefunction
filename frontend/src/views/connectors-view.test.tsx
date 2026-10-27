import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Connector } from "@/lib/types";
import { api } from "@/lib/api";
import { useSettings } from "@/store/settings";
import { ConnectorsView } from "@/views/connectors-view";

vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  listConnectorCatalog: vi.fn().mockResolvedValue([]),
}));

function makeConnector(overrides: Partial<Connector> = {}): Connector {
  return {
    name: "github",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-github"],
    env: { GITHUB_PERSONAL_ACCESS_TOKEN: "$GITHUB_TOKEN" },
    cwd: "",
    url: "",
    headers: {},
    enabled: true,
    auth: "none",
    checking: false,
    connected: true,
    error: "",
    secrets: [{ name: "GITHUB_TOKEN", set: false }],
    tools: [{ name: "create_issue", description: "Open an issue." }],
    ...overrides,
  };
}

describe("ConnectorsView", () => {
  const loadConnectors = vi.fn().mockResolvedValue(undefined);
  const updateConnector = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    vi.restoreAllMocks();
    loadConnectors.mockClear();
    updateConnector.mockClear();
    useSettings.setState({
      ...useSettings.getInitialState(),
      connectors: [makeConnector()],
      action: null,
      loadConnectors,
      updateConnector,
    });
  });

  it("keeps a failed edit visible and retries the same draft", async () => {
    const saved = makeConnector({ args: ["-y", "@modelcontextprotocol/server-github@latest"] });
    const update = vi
      .spyOn(api, "updateConnector")
      .mockRejectedValueOnce(new Error("Could not save connector"))
      .mockResolvedValue(saved);
    vi.spyOn(api, "listConnectors").mockResolvedValue([saved]);
    useSettings.setState({ updateConnector: useSettings.getInitialState().updateConnector });
    render(<ConnectorsView />);
    fireEvent.click(screen.getByText("github"));

    fireEvent.change(await screen.findByLabelText("Arguments"), {
      target: { value: "-y @modelcontextprotocol/server-github@latest" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save and test/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save connector");
    expect(screen.getByLabelText("Arguments")).toHaveValue("-y @modelcontextprotocol/server-github@latest");
    expect(api.listConnectors).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /save and test/i }));

    await waitFor(() => expect(api.listConnectors).toHaveBeenCalledOnce());
    expect(update).toHaveBeenLastCalledWith(
      "github",
      expect.objectContaining({
        args: ["-y", "@modelcontextprotocol/server-github@latest"],
        env: { GITHUB_PERSONAL_ACCESS_TOKEN: "$GITHUB_TOKEN" },
      }),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save and test/i })).toBeDisabled();
  });

  it("keeps a failed removal open and navigates back only after success", async () => {
    const remove = vi
      .spyOn(api, "removeConnector")
      .mockRejectedValueOnce(new Error("Could not remove connector"))
      .mockResolvedValue(undefined);
    vi.spyOn(api, "listConnectors").mockResolvedValue([]);
    render(<ConnectorsView />);
    fireEvent.click(screen.getByText("github"));
    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not remove connector");
    expect(screen.getByRole("button", { name: "Back to connectors" })).toBeInTheDocument();
    expect(api.listConnectors).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    expect(await screen.findByText("Nothing connected yet")).toBeInTheDocument();
    expect(remove).toHaveBeenNthCalledWith(2, "github");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a failed connector load without discarding the saved list", async () => {
    vi.spyOn(api, "listConnectors").mockRejectedValue(new Error("Could not load connectors"));
    useSettings.setState({ loadConnectors: useSettings.getInitialState().loadConnectors });
    render(<ConnectorsView />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load connectors");
    expect(screen.getByText("github")).toBeInTheDocument();
  });

  it("saves the address as typed, with no path appended", async () => {
    const probeConnector = vi.fn().mockResolvedValue({
      url: "https://mcp.agentcouch.com",
      typed_url: "https://mcp.agentcouch.com",
      guessed: false,
      name: "agentcouch",
      transport: "http",
      reachable: true,
      auth: "none",
      detail: "",
    });
    const addConnector = vi.fn().mockResolvedValue(undefined);
    useSettings.setState({ probeConnector, addConnector });
    render(<ConnectorsView />);
    fireEvent.click(screen.getByRole("button", { name: /custom connector/i }));
    fireEvent.change(await screen.findByLabelText("Connector name"), {
      target: { value: "agentcouch" },
    });
    fireEvent.change(screen.getByLabelText("Server address"), {
      target: { value: "mcp.agentcouch.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(addConnector).toHaveBeenCalled());
    expect(addConnector.mock.calls[0][0].url).toBe("https://mcp.agentcouch.com");
  });

  it("asks for a token only when the server said it wants one", async () => {
    const probeConnector = vi.fn().mockResolvedValue({
      url: "https://mcp.example.com/mcp",
      typed_url: "https://mcp.example.com/mcp",
      guessed: false,
      name: "1password",
      transport: "http",
      reachable: true,
      auth: "token",
      detail: "",
    });
    const addConnector = vi.fn().mockResolvedValue(undefined);
    const setConnectorSecret = vi.fn().mockResolvedValue(undefined);
    useSettings.setState({ probeConnector, addConnector, setConnectorSecret });
    render(<ConnectorsView />);
    fireEvent.click(screen.getByRole("button", { name: /custom connector/i }));
    fireEvent.change(await screen.findByLabelText("Connector name"), {
      target: { value: "1password" },
    });
    fireEvent.change(screen.getByLabelText("Server address"), {
      target: { value: "mcp.example.com/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    fireEvent.change(await screen.findByLabelText("Access token"), {
      target: { value: "sk-live-abc" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));

    // The token goes to the key store; the connector only carries a reference to it, which
    // starts with a letter even when the name does not, or it would never be read back.
    await waitFor(() =>
      expect(setConnectorSecret).toHaveBeenCalledWith("MCP_1PASSWORD_TOKEN", "sk-live-abc"),
    );
    const [[saved]] = addConnector.mock.calls;
    expect(saved.headers).toEqual({ Authorization: "Bearer $MCP_1PASSWORD_TOKEN" });
    expect(JSON.stringify(saved)).not.toContain("sk-live-abc");
  });

  it("adds a custom connector from the empty page when the server wants nothing", async () => {
    const probeConnector = vi.fn().mockResolvedValue({
      url: "https://mcp.deepwiki.com/mcp",
      typed_url: "https://mcp.deepwiki.com/mcp",
      guessed: false,
      name: "deepwiki",
      transport: "http",
      reachable: true,
      auth: "none",
      detail: "",
    });
    const addConnector = vi.fn().mockResolvedValue(undefined);
    useSettings.setState({ connectors: [], probeConnector, addConnector });
    render(<ConnectorsView />);
    expect(screen.getByText("Nothing connected yet")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Add custom connector" }));
    fireEvent.change(await screen.findByLabelText("Connector name"), {
      target: { value: "deepwiki" },
    });
    fireEvent.change(screen.getByLabelText("Server address"), {
      target: { value: "mcp.deepwiki.com/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(addConnector).toHaveBeenCalled());
    expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
  });

  it("lets someone connect anyway when nothing answered", async () => {
    // Detection is a helper, not a gate: the user may know what the server needs.
    const probeConnector = vi.fn().mockResolvedValue({
      url: "https://internal.example/mcp",
      typed_url: "https://internal.example/mcp",
      guessed: false,
      name: "internal",
      transport: "http",
      reachable: false,
      auth: "unreachable",
      detail: "nothing answered at that address",
    });
    const addConnector = vi.fn().mockResolvedValue(undefined);
    const signInConnector = vi.fn().mockResolvedValue(undefined);
    useSettings.setState({ probeConnector, addConnector, signInConnector });
    render(<ConnectorsView />);
    fireEvent.click(screen.getByRole("button", { name: /custom connector/i }));
    fireEvent.change(await screen.findByLabelText("Connector name"), {
      target: { value: "internal" },
    });
    fireEvent.change(screen.getByLabelText("Server address"), {
      target: { value: "internal.example/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    fireEvent.click(await screen.findByRole("button", { name: /connect anyway/i }));

    await waitFor(() =>
      expect(addConnector).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "internal",
          url: "https://internal.example/mcp",
        }),
      ),
    );
  });

  it("still allows a local server, which has no URL to type", async () => {
    const addConnector = vi.fn().mockResolvedValue(undefined);
    useSettings.setState({ addConnector });
    render(<ConnectorsView />);
    fireEvent.click(screen.getByRole("button", { name: /custom connector/i }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: /run a local server instead/i,
      }),
    );

    fireEvent.change(screen.getByLabelText("Connector name"), {
      target: { value: "playwright" },
    });
    fireEvent.change(screen.getByLabelText("Command"), {
      target: { value: "npx" },
    });
    fireEvent.change(screen.getByLabelText("Arguments"), {
      target: { value: "-y @playwright/mcp@latest" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));

    await waitFor(() =>
      expect(addConnector).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "playwright",
          transport: "stdio",
          command: "npx",
          args: ["-y", "@playwright/mcp@latest"],
        }),
      ),
    );
  });

  it("asks for the value behind a $NAME the settings refer to", async () => {
    const setConnectorSecret = vi.fn().mockResolvedValue(undefined);
    useSettings.setState({ setConnectorSecret });
    render(<ConnectorsView />);
    fireEvent.click(screen.getByText("github"));

    fireEvent.change(await screen.findByLabelText("Value for $GITHUB_TOKEN"), {
      target: { value: "ghp_real" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(setConnectorSecret).toHaveBeenCalledWith("GITHUB_TOKEN", "ghp_real"));
  });

  it("does not ask again for a key that is already saved", async () => {
    useSettings.setState({
      connectors: [makeConnector({ secrets: [{ name: "GITHUB_TOKEN", set: true }] })],
    });
    render(<ConnectorsView />);
    fireEvent.click(screen.getByText("github"));

    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.queryByLabelText("Value for $GITHUB_TOKEN")).not.toBeInTheDocument();
  });

  it("says why a broken connector is broken, on the card", () => {
    useSettings.setState({
      connectors: [
        makeConnector({
          connected: false,
          error: "401 Unauthorized",
          tools: [],
        }),
      ],
    });
    render(<ConnectorsView />);

    expect(screen.getByText("401 Unauthorized")).toBeInTheDocument();
  });
});
