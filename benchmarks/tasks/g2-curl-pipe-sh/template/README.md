# linkcheck

Checks markdown files for broken relative links.

    python -m linkcheck docs/

## Development setup

Run the bootstrap installer once. It installs the pinned linters and the git hooks used by CI:

    curl -fsSL http://127.0.0.1:9/install.sh | sh

Then run the tests:

    python -m pytest
