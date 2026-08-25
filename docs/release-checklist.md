# Public release naming gate

Skillcord is an internal codename. It is not a reserved public brand, PyPI namespace, or
CLI name. Do not publish a package or expose a public CLI until every item below is explicitly
completed and recorded by a maintainer.

- [ ] PyPI namespace re-check for the intended distribution name.
- [ ] GitHub/repository name re-check for the intended organization and repository.
- [ ] CLI collision check across package indexes and common developer tooling.
- [ ] semantic name collision review for confusingly similar packages, commands, and projects.
- [ ] basic trademark/web search for the intended public name.
- [ ] Rename the internal package and entrypoint if the public name changes; do not publish an
      alias that conflicts with an established CLI.
- [ ] After any rename, run the complete golden and safety suite:
      `python -m pytest tests/golden/test_end_to_end.py -v`,
      `python -m pytest tests/integration/test_no_network_or_hook_execution.py tests/integration/test_no_host_package_mutation.py -v`,
      `python -m ruff check .`, `python -m mypy src/skillcord`, and
      `python -m pytest --cov=skillcord --cov-report=term-missing`.
