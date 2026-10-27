import { useState } from "react";
import {
  localAccountGroups,
  localConnectorIds,
  localConnectorProducts,
  matchesLocalIntegration,
  type LocalConnectorId,
} from "@/lib/connector-presentation";
import { ConnectorLogo, InstalledTag, LoginMethodTag } from "@/components/connector-brand";
import { GoogleAccountSetup } from "@/components/google-setup";
import { MicrosoftSetup } from "@/components/microsoft-setup";
import { EmailSetup } from "@/components/email-setup";
import { useSettings } from "@/store/settings";

export function LocalIntegrations({
  query = "",
  filter = "all",
  section,
}: {
  query?: string;
  filter?: string;
  section?: "Email" | "Workspace";
}) {
  const [selected, setSelected] = useState<LocalConnectorId | null>(null);
  const connectors = useSettings((state) => state.connectors);
  const accounts = localAccountGroups(connectors);
  const visible = localConnectorIds.filter((id) => matchesLocalIntegration(id, query, filter));
  const close = () => setSelected(null);
  return (
    <>
      {(["Email", "Workspace"] as const)
        .filter((title) => !section || title === section)
        .map((title) => {
          const cards = visible.filter((id) => localConnectorProducts[id].section === title);
          if (!cards.length) return null;
          return (
            <section key={title} aria-label={title} className="space-y-3 pt-4">
              <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {cards.map((id) => {
                  const product = localConnectorProducts[id];
                  const installed = accounts.some((account) => account.product === id);
                  return (
                    <button
                      key={id}
                      aria-label={`${product.title}. ${product.description}${installed ? " Installed." : ""}`}
                      onClick={() => setSelected(id)}
                      className="rounded-xl border border-hairline bg-card p-4 text-left transition-colors hover:bg-muted"
                    >
                      <span className="flex items-center gap-3 text-sm font-medium">
                        <ConnectorLogo id={product.logo} />
                        {product.title}
                        {installed ? <InstalledTag /> : null}
                      </span>
                      <span className="mt-3 block text-xs text-muted-foreground">{product.description}</span>
                      <span className="mt-3 flex items-center gap-2">
                        <LoginMethodTag>{product.login}</LoginMethodTag>
                        <span className="text-2xs text-muted-foreground">Runs locally</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      {selected === "gmail" || selected === "email" ? (
        <EmailSetup initialProvider={selected === "gmail" ? "gmail" : "other"} close={close} />
      ) : selected === "google" ? (
        <GoogleAccountSetup close={close} />
      ) : selected === "outlook" || selected === "microsoft365" ? (
        <MicrosoftSetup mode={selected === "outlook" ? "mail" : "workspace"} close={close} />
      ) : null}
    </>
  );
}
