from __future__ import annotations


CLOSEOUT_COMMAND_ORDER = [
    "cd ~/Developer/bebe",
    "scripts/ninoctl finish --key-stdin",
    "scripts/ninoctl finish --key-env",
    "scripts/ninoctl finish --skip-configure",
    "scripts/ninoctl configure-claude --keychain-service nino-anthropic",
    "scripts/nino-launchd stop",
    "scripts/nino-launchd start",
    "scripts/ninoctl final-audit",
]


def ordered_next_commands(commands: list[str]) -> list[str]:
    first_seen: dict[str, int] = {}
    for index, command in enumerate(commands):
        first_seen.setdefault(command, index)

    priority = {command: index for index, command in enumerate(CLOSEOUT_COMMAND_ORDER)}
    unknown_offset = len(priority)
    return sorted(
        first_seen,
        key=lambda command: (
            priority.get(command, unknown_offset + first_seen[command]),
            first_seen[command],
        ),
    )
