"""Per-profile permissions for receiving tasks and delivering results."""
from dataclasses import dataclass

from agent.i18n import t


def _sources(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(t("super_agent.invalid_sources"))
    return frozenset(item.strip().lower() for item in value if item.strip())


@dataclass(frozen=True)
class MessageSources:
    read: frozenset[str]
    write: frozenset[str]

    @classmethod
    def from_config(cls, ctx):
        read = _sources(ctx.get_config("message_sources", ["*"]))
        configured = ctx.get_config("write_message_sources", ["same-as-read"])
        return cls(read, read if configured == ["same-as-read"] else _sources(configured))

    def reads(self, platform):
        return "*" in self.read or platform in self.read

    def writes(self, platform):
        return "*" in self.write or platform in self.write

    def write_only(self, ctx):
        if self.write == self.read or "*" in self.read:
            return ()
        platforms = ctx.home_channel_platforms(require_gateway=True) if "*" in self.write else self.write
        return sorted(platform for platform in platforms if not self.reads(platform))
