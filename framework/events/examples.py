from __future__ import annotations

from framework.events.base import RegexEvent


class EventLogin(RegexEvent):
    def __init__(self) -> None:
        super().__init__(pattern=r"(?i)login", name="EventLogin")


class EventLoginSuccess(RegexEvent):
    def __init__(self) -> None:
        super().__init__(
            pattern=r"(?i)(login successful|welcome|last login)",
            name="EventLoginSuccess",
        )


class EventAccept(RegexEvent):
    def __init__(self) -> None:
        super().__init__(pattern=r"(?i)(accept|yes/no)", name="EventAccept")