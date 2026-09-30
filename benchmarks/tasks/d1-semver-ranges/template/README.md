# semverlite

Minimal semantic-version helpers used by our plugin loader to pick compatible plugin releases.

```python
from semverlite import compare, max_satisfying, satisfies, sort_versions

compare("1.2.3", "1.2.4")                         # -1
satisfies("1.4.0", "^1.2.3")                      # True
max_satisfying(["1.2.3", "1.9.0", "2.0.0"], "~1.2.0 || ^1.5.0")   # "1.9.0"
```

The behaviour is specified in [docs/SPEC.md](docs/SPEC.md).
