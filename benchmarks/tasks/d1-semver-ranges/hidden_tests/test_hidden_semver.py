"""Hidden tests for d1: every rule of docs/SPEC.md."""
import pytest

from semverlite import compare, max_satisfying, parse, satisfies, sort_versions

# ---------------------------------------------------------------- 1. syntax
@pytest.mark.parametrize("text", [
    "0.0.0", "1.2.3", "v1.2.3", "  1.2.3  ", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-0.3.7",
    "1.0.0-x.7.z.92", "1.0.0-x-y-z.--", "1.0.0-0a", "1.0.0+20130313144700", "1.0.0-beta+exp.sha.5114f85",
    "1.0.0+21AF26D3----117B344092BD", "1.0.0+001",
])
def test_valid_versions(text):
    parse(text)


@pytest.mark.parametrize("text", [
    "", "1.2", "1.2.3.4", "1.2.3-", "1.2.3-a..b", "1.2.3+", "1.2.3+a..b", "01.2.3", "1.02.3", "1.2.03",
    "1.2.3-01", "1.2.3-be$ta", "vv1.2.3", "a.b.c", "1.2.3 4", "-1.2.3",
])
def test_invalid_versions(text):
    with pytest.raises(ValueError):
        parse(text)


# ---------------------------------------------------------------- 2. precedence
CHAIN = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
         "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]


@pytest.mark.parametrize("lo, hi", list(zip(CHAIN, CHAIN[1:])))
def test_spec_chain(lo, hi):
    assert compare(lo, hi) == -1
    assert compare(hi, lo) == 1


@pytest.mark.parametrize("a, b, expected", [
    ("1.10.0", "1.9.0", 1),
    ("2.0.0", "10.0.0", -1),
    ("1.2.10", "1.2.9", 1),
    ("1.0.0+a", "1.0.0+b", 0),
    ("v1.0.0", "1.0.0", 0),
    ("1.0.0-2", "1.0.0-10", -1),         # numeric identifiers compare numerically
    ("1.0.0-alpha", "1.0.0-1", 1),        # numeric < alphanumeric
    ("1.0.0-a.b", "1.0.0-a", 1),          # more identifiers wins
    ("1.0.0-B", "1.0.0-a", -1),           # ASCII order
    ("0.9.9", "1.0.0-alpha", -1),
])
def test_compare(a, b, expected):
    assert compare(a, b) == expected


def test_sort_versions():
    shuffled = ["1.0.0", "1.0.0-beta.11", "1.0.0-alpha", "1.0.0-rc.1", "1.0.0-beta",
                "1.0.0-alpha.beta", "1.0.0-beta.2", "1.0.0-alpha.1"]
    assert sort_versions(shuffled) == CHAIN
    assert sort_versions(["1.10.0", "1.9.0", "1.2.3"]) == ["1.2.3", "1.9.0", "1.10.0"]


def test_sort_versions_stable_and_returns_input_elements():
    out = sort_versions(["1.0.0+b", "0.1.0", "1.0.0+a"])
    assert out == ["0.1.0", "1.0.0+b", "1.0.0+a"]


# ---------------------------------------------------------------- 3. ranges
@pytest.mark.parametrize("version, rng, expected", [
    ("1.4.0", "^1.2.3", True),
    ("2.0.0", "^1.2.3", False),
    ("1.2.2", "^1.2.3", False),
    ("0.2.9", "^0.2.3", True),
    ("0.3.0", "^0.2.3", False),
    ("0.0.3", "^0.0.3", True),
    ("0.0.4", "^0.0.3", False),
    ("1.2.9", "~1.2.3", True),
    ("1.3.0", "~1.2.3", False),
    ("2.2.5", "~1.2.3", False),           # tilde must not ignore MAJOR
    ("0.1.5", "~0.1.2", True),
    ("1.2.3", "1.2.3", True),
    ("1.2.3+build.7", "=1.2.3", True),    # build ignored
    ("1.2.4", ">1.2.3 <1.3.0", True),
    ("1.3.0", ">1.2.3 <1.3.0", False),
    ("1.2.3", ">=1.2.3 <=1.2.3", True),
    ("3.1.0", "^1.2.3 || ^3.0.0", True),
    ("2.5.0", "^1.2.3 || ^3.0.0", False),
    ("0.5.0", "<0.1.0 || >=0.5.0 <0.6.0", True),
    ("7.0.0", "*", True),
    # pre-release rule
    ("1.2.3-beta.2", ">=1.2.3-beta.1", True),
    ("1.2.4-beta.1", ">=1.2.3-beta.1", False),
    ("1.2.3-beta.4", "^1.2.3-beta.1", True),
    ("1.3.0-beta.1", "^1.2.3-beta.1", False),
    ("1.2.3-alpha", "^1.2.3-beta.1", False),
    ("2.0.0-rc.1", "*", False),
    ("2.0.0-rc.1", "<2.0.0", False),
    ("1.9.0-rc.1", "^1.2.3", False),
    ("1.2.3-rc.1", "~1.2.3-alpha", True),
    ("1.2.3-rc.1", ">=1.0.0 <1.2.3-rc.5", True),   # same tuple + pre-release in the upper bound
    ("1.2.3-rc.1", "<1.0.0 || >=1.2.3-rc.0", True),
    ("1.2.3-rc.1", ">=1.2.3-rc.0 || <1.0.0", True),
    ("1.2.3-rc.1", "<1.2.3-rc.0 || >=1.0.0", False),
])
def test_satisfies(version, rng, expected):
    assert satisfies(version, rng) is expected


@pytest.mark.parametrize("rng", ["", "   ", "1.2", "^1.2", ">= 1.2.3", "1.2.3 ||", "|| 1.2.3", "=>1.2.3",
                                 "~>1.2.3", "!1.2.3"])
def test_invalid_ranges(rng):
    with pytest.raises(ValueError):
        satisfies("1.2.3", rng)


def test_max_satisfying():
    vs = ["1.2.3", "1.9.0", "1.10.0", "2.0.0", "2.0.0-rc.1", "1.10.1-beta"]
    assert max_satisfying(vs, "^1.2.3") == "1.10.0"
    assert max_satisfying(vs, "~1.2.0 || ^1.5.0") == "1.10.0"
    assert max_satisfying(vs, ">=2.0.0-rc.0") == "2.0.0"
    assert max_satisfying(vs, "^3.0.0") is None
    assert max_satisfying(["v1.0.0", "1.0.0-rc.1"], "*") == "v1.0.0"


def test_max_satisfying_invalid_entry():
    with pytest.raises(ValueError):
        max_satisfying(["1.2.3", "1.2"], "*")
