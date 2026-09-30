"""Conversion funnel."""


def conversion(events: list[dict], steps: list[str]) -> list[float]:
    """Share of users reaching each step, relative to the first step."""
    users_per_step = [{e["user"] for e in events if e["name"] == s} for s in steps]
    base = len(users_per_step[0]) or 1
    reached = users_per_step[0]
    out = []
    for users in users_per_step:
        reached = reached & users
        out.append(round(len(reached) / base, 4))
    return out
