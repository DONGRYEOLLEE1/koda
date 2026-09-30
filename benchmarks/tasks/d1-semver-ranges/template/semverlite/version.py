"""Version parsing and precedence. See docs/SPEC.md sections 1-2."""
import re

_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")


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

    def key(self):
        return (str(self.major), str(self.minor), str(self.patch), ".".join(self.prerelease))


def parse(text) -> Version:
    if isinstance(text, Version):
        return text
    m = _RE.match(text.strip())
    if not m:
        raise ValueError(f"invalid version: {text!r}")
    major, minor, patch, pre, build = m.groups()
    return Version(int(major), int(minor), int(patch),
                   pre.split(".") if pre else (), build.split(".") if build else ())


def compare(a, b) -> int:
    ka, kb = parse(a).key(), parse(b).key()
    return (ka > kb) - (ka < kb)


def sort_versions(versions):
    return sorted(versions, key=lambda v: parse(v).key())
