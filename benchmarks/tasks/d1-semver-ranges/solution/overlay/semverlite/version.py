"""Version parsing and precedence. See docs/SPEC.md sections 1-2."""
import functools
import re

_NUM = r"0|[1-9]\d*"
_IDENT = r"[0-9A-Za-z-]+"
_RE = re.compile(
    rf"^v?({_NUM})\.({_NUM})\.({_NUM})"
    rf"(?:-({_IDENT}(?:\.{_IDENT})*))?"
    rf"(?:\+({_IDENT}(?:\.{_IDENT})*))?$"
)


class Version:
    def __init__(self, major, minor, patch, prerelease=(), build=()):
        self.major, self.minor, self.patch = major, minor, patch
        self.prerelease = tuple(prerelease)
        self.build = tuple(build)

    def __str__(self):
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            s += "-" + ".".join(self.prerelease)
        if self.build:
            s += "+" + ".".join(self.build)
        return s

    def __repr__(self):
        return f"Version({str(self)!r})"

    @property
    def core(self):
        return (self.major, self.minor, self.patch)

    def key(self):
        return _Key(self)


def parse(text) -> Version:
    if isinstance(text, Version):
        return text
    if not isinstance(text, str):
        raise ValueError(f"invalid version: {text!r}")
    m = _RE.match(text.strip())
    if not m:
        raise ValueError(f"invalid version: {text!r}")
    major, minor, patch, pre, build = m.groups()
    pre_ids = pre.split(".") if pre else []
    for ident in pre_ids:
        if ident.isdigit() and len(ident) > 1 and ident.startswith("0"):
            raise ValueError(f"invalid version (leading zero in pre-release): {text!r}")
    return Version(int(major), int(minor), int(patch), pre_ids, build.split(".") if build else [])


def _cmp_ident(a: str, b: str) -> int:
    an, bn = a.isdigit(), b.isdigit()
    if an and bn:
        return (int(a) > int(b)) - (int(a) < int(b))
    if an != bn:
        return -1 if an else 1
    return (a > b) - (a < b)


def _cmp(va: Version, vb: Version) -> int:
    if va.core != vb.core:
        return (va.core > vb.core) - (va.core < vb.core)
    pa, pb = va.prerelease, vb.prerelease
    if not pa or not pb:
        return (not pa) - (not pb)          # release > pre-release
    for x, y in zip(pa, pb):
        c = _cmp_ident(x, y)
        if c:
            return c
    return (len(pa) > len(pb)) - (len(pa) < len(pb))


@functools.total_ordering
class _Key:
    def __init__(self, v):
        self.v = v

    def __eq__(self, other):
        return _cmp(self.v, other.v) == 0

    def __lt__(self, other):
        return _cmp(self.v, other.v) < 0


def compare(a, b) -> int:
    return _cmp(parse(a), parse(b))


def sort_versions(versions):
    return sorted(versions, key=lambda v: parse(v).key())
