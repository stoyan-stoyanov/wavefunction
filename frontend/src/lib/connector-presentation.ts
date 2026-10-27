import type { Connector } from "@/lib/types";

export const connectorCategories = [
  { title: "Agent collaboration", ids: ["agentcouch"] },
  { title: "Email", ids: [] },
  { title: "Workspace", ids: [] },
  {
    title: "Productivity & collaboration",
    ids: [
      "airtable",
      "asana",
      "atlassian",
      "clickup",
      "craft",
      "dropbox",
      "fireflies",
      "granola",
      "linear",
      "miro",
      "monday",
      "notion",
      "slack",
      "todoist",
    ],
  },
  {
    title: "Design & content",
    ids: ["canva", "cloudinary", "comfy-cloud", "figma-desktop", "gamma", "webflow", "wordpress-com"],
  },
  {
    title: "Sales & customer support",
    ids: ["attio", "close", "hubspot", "salesforce", "intercom", "klaviyo"],
  },
  {
    title: "Data & analytics",
    ids: [
      "algolia",
      "amplitude",
      "hex",
      "snowflake",
      "mixpanel",
      "motherduck",
      "neon",
      "posthog",
      "prisma-postgres",
      "supabase",
    ],
  },
  {
    title: "Development & automation",
    ids: [
      "buildkite",
      "circleci",
      "cloudflare",
      "expo",
      "gitlab",
      "heroku",
      "hugging_face",
      "n8n",
      "netlify",
      "postman",
      "railway",
      "render",
      "resend",
      "unreal-engine",
      "vercel",
    ],
  },
  {
    title: "Monitoring & security",
    ids: ["betterstack", "datadog", "globalping", "grafana", "semgrep", "sentry", "socket"],
  },
  { title: "Finance & payments", ids: ["paypal", "plaid", "robinhood", "square", "stripe", "twelve-data"] },
  {
    title: "Research & documentation",
    ids: ["aws-knowledge", "context7", "deepwiki", "microsoft-learn", "twilio-docs", "wolfram"],
  },
  { title: "Travel & lifestyle", ids: ["alltrails", "kiwi", "trivago"] },
];

export function connectorLogoId(name: string): string {
  if (name.startsWith("microsoft-mail-")) return "outlook";
  if (name.startsWith("microsoft-")) return "microsoft-workspace";
  if (name.startsWith("mail-")) return "email";
  if (name.startsWith("google-")) return "google-workspace";
  return name;
}

export function loginLabel(auth: string): string {
  return auth === "oauth" ? "OAuth" : auth === "api_key" ? "API key" : "No login";
}

export const localConnectorProducts = {
  gmail: {
    title: "Gmail",
    section: "Email",
    logo: "gmail",
    description: "Read, send, and organize Gmail.",
    login: "App password",
    keywords: "Himalaya Google mail",
  },
  outlook: {
    title: "Outlook",
    section: "Email",
    logo: "outlook",
    description: "Read, send, and organize Outlook email.",
    login: "OAuth",
    keywords: "Microsoft Hotmail Live Graph mail",
  },
  email: {
    title: "Email",
    section: "Email",
    logo: "email",
    description: "Connect other email providers with IMAP and SMTP.",
    login: "Password / app password",
    keywords: "Himalaya other mail",
  },
  google: {
    title: "Google",
    section: "Workspace",
    logo: "google-workspace",
    description: "Connect Drive, Docs, Sheets, Calendar, and Gmail.",
    login: "OAuth",
    keywords: "Google Workspace gog files mail",
  },
  microsoft365: {
    title: "Microsoft 365",
    section: "Workspace",
    logo: "microsoft-workspace",
    description: "Connect OneDrive, Calendar, SharePoint, and Excel.",
    login: "OAuth",
    keywords: "Microsoft Office Graph files documents",
  },
} as const;

export type LocalConnectorId = keyof typeof localConnectorProducts;
export const localConnectorIds = Object.keys(localConnectorProducts) as LocalConnectorId[];

export function matchesLocalIntegration(id: LocalConnectorId, query: string, filter: string): boolean {
  const product = localConnectorProducts[id];
  const text = `${product.title} ${product.description} ${product.keywords}`;
  return (
    text.toLowerCase().includes(query.trim().toLowerCase()) &&
    (filter === "all" || filter === "local" || (filter === "oauth" && product.login === "OAuth"))
  );
}

export function localConnectorAccount(connector: Connector) {
  const kind = connector.env?.WAVEFUNCTION_LOCAL_INTEGRATION;
  const argument = (flag: string) => {
    const index = connector.args.indexOf(flag);
    return index >= 0 ? (connector.args[index + 1] ?? "") : "";
  };
  const email = argument("--account");
  if (!email || !["gog", "himalaya", "microsoft"].includes(kind)) return null;
  const provider =
    kind === "microsoft"
      ? "microsoft"
      : kind === "gog" ||
          connector.env.WAVEFUNCTION_ACCOUNT_PROVIDER === "google" ||
          /@(gmail|googlemail)\.com$/i.test(email)
        ? "google"
        : "email";
  const selected =
    kind === "gog"
      ? argument("--allow-tool") || "drive,docs,sheets,calendar"
      : argument("--services") ||
        (argument("--mode") === "mail" || kind === "himalaya" ? "mail" : "mail,files,calendar");
  const services = selected.split(",");
  const product: LocalConnectorId =
    kind === "microsoft"
      ? argument("--mode") === "mail"
        ? "outlook"
        : "microsoft365"
      : kind === "gog"
        ? "google"
        : provider === "google"
          ? "gmail"
          : "email";
  return {
    provider,
    product,
    email,
    services: [
      // The Google setup saves Gmail as "gmail".
      ...(services.includes("mail") || services.includes("gmail") ? ["Mail"] : []),
      ...(services.some((service) => ["drive", "docs", "sheets", "files"].includes(service))
        ? ["Files"]
        : []),
      ...(services.includes("calendar") ? ["Calendar"] : []),
    ],
  };
}

export function localAccountGroups(connectors: Connector[]) {
  const groups = new Map<
    string,
    {
      provider: string;
      product: LocalConnectorId;
      email: string;
      services: string[];
      connectors: Connector[];
    }
  >();
  for (const connector of connectors) {
    const account = localConnectorAccount(connector);
    if (!account) continue;
    // Older custom-domain Gmail profiles can be paired with their existing Google connection.
    if (
      account.provider === "email" &&
      connectors.some(
        (other) =>
          other.env?.WAVEFUNCTION_LOCAL_INTEGRATION === "gog" &&
          localConnectorAccount(other)?.email.toLowerCase() === account.email.toLowerCase(),
      )
    ) {
      account.provider = "google";
      account.product = "gmail";
    }
    const key = `${account.product}:${account.email.toLowerCase()}`;
    const group = groups.get(key) ?? { ...account, services: [], connectors: [] };
    group.services = [...new Set([...group.services, ...account.services])];
    group.connectors.push(connector);
    groups.set(key, group);
  }
  return [...groups.values()];
}
