/** User-owned internal app for Slack MCP. All scopes apply to the signing-in user. */
export function slackManifest(callback: string, scopes: string) {
  return JSON.stringify(
    {
      display_information: {
        name: "Wavefunction",
        description: "Connect Wavefunction to this Slack workspace.",
      },
      oauth_config: {
        redirect_urls: [callback],
        pkce_enabled: true,
        scopes: { user: [...new Set(scopes.split(/[\s,]+/).filter(Boolean))] },
      },
      // No token rotation: Slack makes it an extra step in App Settings, and PKCE does not need it.
      // A rotating token would still be refreshed; without one, the token does not expire.
      settings: { is_mcp_enabled: true, socket_mode_enabled: false },
    },
    null,
    2,
  );
}
