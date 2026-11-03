"""Collected numbers the report needs. Printed by conftest at the end of a run."""

METRICS: dict[str, str] = {}


def record(name: str, value) -> None:
    METRICS[name] = value if isinstance(value, str) else f"{value}"
