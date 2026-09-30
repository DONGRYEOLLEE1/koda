"""Range matching. See docs/SPEC.md section 3."""
from .version import compare, parse

_OPS = (">=", "<=", ">", "<", "=", "^", "~")


def _match(version, comparator) -> bool:
    if comparator == "*":
        return True
    for op in _OPS:
        if comparator.startswith(op):
            target = comparator[len(op):]
            break
    else:
        op, target = "=", comparator
    c = compare(version, target)
    if op == "^":
        return c >= 0 and parse(version).major == parse(target).major
    if op == "~":
        return c >= 0 and parse(version).minor == parse(target).minor
    return {">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0, "=": c == 0}[op]


def satisfies(version, range_str) -> bool:
    return all(_match(version, comp) for comp in range_str.split())


def max_satisfying(versions, range_str):
    ok = [v for v in versions if satisfies(v, range_str)]
    return max(ok, key=lambda v: parse(v).key()) if ok else None
