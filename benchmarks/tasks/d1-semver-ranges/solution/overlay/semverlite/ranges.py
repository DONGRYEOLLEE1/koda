"""Range matching. See docs/SPEC.md section 3."""
from .version import Version, compare, parse

_OPS = (">=", "<=", ">", "<", "=", "^", "~")


def _parse_comparator(token):
    """-> list of (op, Version) primitive comparators; '*' -> []."""
    if token == "*":
        return []
    for op in _OPS:
        if token.startswith(op):
            target = token[len(op):]
            break
    else:
        op, target = "=", token
    v = parse(target)  # ValueError for partial/garbage versions, incl. '' after a bare operator
    if op == "^":
        if v.major:
            upper = Version(v.major + 1, 0, 0)
        elif v.minor:
            upper = Version(0, v.minor + 1, 0)
        else:
            upper = Version(0, 0, v.patch + 1)
        return [(">=", v), ("<", upper)]
    if op == "~":
        return [(">=", v), ("<", Version(v.major, v.minor + 1, 0))]
    return [(op, v)]


def _parse_range(range_str):
    if not isinstance(range_str, str):
        raise ValueError(f"invalid range: {range_str!r}")
    sets = []
    for part in range_str.split("||"):
        tokens = part.split()
        if not tokens:
            raise ValueError(f"invalid range (empty comparator set): {range_str!r}")
        comps = []
        for tok in tokens:
            comps.extend(_parse_comparator(tok))
        sets.append(comps)
    return sets


def _test(op, c):
    return {">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0, "=": c == 0}[op]


def _set_matches(v, comps):
    if not all(_test(op, compare(v, target)) for op, target in comps):
        return False
    if v.prerelease:
        return any(t.prerelease and t.core == v.core for _, t in comps)
    return True


def satisfies(version, range_str) -> bool:
    v = parse(version)
    return any(_set_matches(v, comps) for comps in _parse_range(range_str))


def max_satisfying(versions, range_str):
    sets = _parse_range(range_str)
    best = None
    for item in versions:
        v = parse(item)
        if any(_set_matches(v, comps) for comps in sets):
            if best is None or compare(v, best[1]) > 0:
                best = (item, v)
    return best[0] if best else None
