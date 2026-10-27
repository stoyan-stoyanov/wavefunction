"""Browser sign-in jobs that survive navigation within the desktop app."""

from __future__ import annotations

import asyncio

from .connectors import ConnectorRegistry, McpServer


class CatalogConnections:
    def __init__(self, registry: ConnectorRegistry):
        self.registry = registry
        self.tasks: dict[str, asyncio.Task] = {}
        self.results: dict[str, dict] = {}

    def start(self, server: McpServer, *, candidate=None, on_success=None):
        if server.name in self.tasks and not self.tasks[server.name].done():
            return
        self.results[server.name] = {"state": "connecting", "error": ""}

        async def run():
            try:
                registry = candidate if candidate is not None else self.registry
                found = await (
                    registry.sign_in(server) if server.auth == "oauth" else registry.refresh(server)
                )
                if found.connected and on_success is not None:
                    on_success(found)
                self.results[server.name] = {
                    "state": "connected" if found.connected else "failed",
                    "error": found.error,
                }
            except asyncio.CancelledError:
                self.results[server.name] = {"state": "cancelled", "error": "Sign-in cancelled."}
                raise
            except Exception:
                self.results[server.name] = {
                    "state": "failed",
                    "error": "Could not finish connecting. Try again or check the provider’s setup instructions.",
                }

        self.tasks[server.name] = asyncio.create_task(run())

    def replace(self, server: McpServer, changes: dict[str, str]):
        from .secrets import StagedSecretStore

        secrets = StagedSecretStore(self.registry.secrets)
        for key, value in changes.items():
            secrets.set(key, value)
        candidate = ConnectorRegistry(self.registry.repository, secrets)
        if server.auth == "oauth":
            candidate.sign_out(server)

        def publish(found):
            secrets.commit()
            self.registry.save(
                [item for item in self.registry.servers() if item.name != server.name] + [server]
            )
            self.registry._cache[server.name] = found

        self.start(server, candidate=candidate, on_success=publish)

    async def cancel(self, name: str):
        task = self.tasks.get(name)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.results[name] = {"state": "cancelled", "error": "Sign-in cancelled."}

    async def close(self):
        await asyncio.gather(*(self.cancel(name) for name in self.tasks))
