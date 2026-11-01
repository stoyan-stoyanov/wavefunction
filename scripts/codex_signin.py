"""The ChatGPT sign-in for live checks: never refreshed, never written."""

import base64
import json

from wavefunction.codex import CodexAuth, CodexAuthError, CodexAuthStore, CodexCredentials


class ReadOnlyAuth(CodexAuth):
    def access_token(self):
        credentials = self.credentials()
        if credentials is None or credentials.expiring():
            raise CodexAuthError("A current sign-in is needed. This check never refreshes credentials.")
        return credentials.access


class ReadOnlyStore(CodexAuthStore):
    """The app's sign-in file, or the Codex CLI's `~/.codex/auth.json`; never written."""

    def load(self):
        tokens = json.loads(self.path.read_text(encoding="utf-8")).get("tokens")
        if not tokens:
            return super().load()
        claims = tokens["access_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
        return CodexCredentials(
            tokens["access_token"], tokens["refresh_token"], float(claims["exp"]), tokens["account_id"]
        )

    def save(self, credentials):
        raise CodexAuthError("This check never writes the sign-in")


def read_only_auth(path):
    return ReadOnlyAuth(ReadOnlyStore(path.expanduser()))
