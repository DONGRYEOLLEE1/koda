# semverlite specification

## 1. Version syntax

`[v]MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]`

- Surrounding whitespace is ignored. A single leading `v` is allowed and ignored (`v1.2.3` is `1.2.3`).
- MAJOR, MINOR and PATCH are non-negative integers **without leading zeros** (`0` is fine, `01` is invalid).
- PRERELEASE is one or more dot-separated identifiers made of `[0-9A-Za-z-]`. Identifiers must not be
  empty. Purely numeric identifiers must not have leading zeros (`1.0.0-01` is invalid, `1.0.0-0a` is fine).
- BUILD is one or more dot-separated non-empty identifiers made of `[0-9A-Za-z-]` (leading zeros allowed).
- Anything else raises `ValueError`, e.g. `""`, `1.2`, `1.2.3.4`, `1.2.3-`, `1.2.3-a..b`, `1.2.3+`,
  `01.2.3`, `1.2.3-01`, `1.2.3-be$ta`.

## 2. Precedence — `compare(a, b)` returns -1, 0 or 1

Follows SemVer 2.0.0 section 11:

1. Compare MAJOR, MINOR and PATCH numerically, in that order.
2. When they are equal, a version **without** PRERELEASE is greater than one with PRERELEASE
   (`1.0.0-rc.1 < 1.0.0`).
3. Two PRERELEASEs are compared identifier by identifier, left to right:
   - both numeric: compared numerically;
   - both alphanumeric: compared in ASCII order;
   - a numeric identifier is lower than an alphanumeric one;
   - if every shared identifier is equal, the PRERELEASE with more identifiers is greater.
4. BUILD metadata is ignored (`1.0.0+a` and `1.0.0+b` compare equal).

Each of these is lower than the next:

    1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0

`sort_versions(versions)` returns a new list sorted ascending by precedence; versions of equal precedence
keep their input order. Elements are returned as given (strings stay strings).

## 3. Ranges — `satisfies(version, range_str)`

- A range is one or more **comparator sets** joined by `||`. The range matches if **any** set matches.
- A comparator set is one or more comparators separated by whitespace. It matches if **all** of its
  comparators match.
- Comparators: `=V`, `V` (same as `=V`), `>V`, `>=V`, `<V`, `<=V`, `^V`, `~V` and `*`, where `V` is a full
  version as in section 1 (no partial versions such as `1.2`). No whitespace between operator and version.
  Anything else (including an empty range or an empty set) raises `ValueError`.
- `^V` (caret) allows changes that do not modify the left-most non-zero part of MAJOR.MINOR.PATCH:
  - `^1.2.3` means `>=1.2.3 <2.0.0`
  - `^0.2.3` means `>=0.2.3 <0.3.0`
  - `^0.0.3` means `>=0.0.3 <0.0.4`
- `~V` (tilde) means `>=V <MAJOR.(MINOR+1).0`, e.g. `~1.2.3` means `>=1.2.3 <1.3.0`.
- `*` matches any version (subject to the pre-release rule).
- **Pre-release rule.** A version that has a PRERELEASE satisfies a comparator set only if at least one
  comparator in *that same set* refers to a version with the same MAJOR.MINOR.PATCH that also has a
  PRERELEASE (for `^V`/`~V` this is the lower bound `V` itself). Otherwise it does not match, even if every
  comparison would succeed. Examples:
  - `1.2.3-beta.2` satisfies `>=1.2.3-beta.1`
  - `1.2.4-beta.1` does **not** satisfy `>=1.2.3-beta.1`
  - `1.2.3-beta.4` satisfies `^1.2.3-beta.1`, `1.3.0-beta.1` does not
  - `2.0.0-rc.1` satisfies neither `*` nor `<2.0.0`
- BUILD metadata of the version is ignored.

`max_satisfying(versions, range_str)` returns the element of `versions` (as given) with the highest
precedence that satisfies the range, or `None`. An invalid entry in `versions` raises `ValueError`.
