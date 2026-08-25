# Provider support in V1

V1 discovers local, declarative provider content and normalizes it into stable,
provider-scoped skill IDs. Discovery is local and read-only: it neither installs software
nor executes provider hooks, setup scripts, daemons, or MCP servers.

| Provider | V1 boundary |
| --- | --- |
| Superpowers | Discovers declarative engineering workflow skills and pins selected content. |
| Everything Claude Code | Discovers supported declarative skills/assets; executable hooks remain inactive and are reported as partial support. |
| Open Design | Discovers declarative skills only; it does not launch or install the Open Design daemon or MCP runtime. |
| Humanizer | Is optional and harness-only: V1 may discover and configure it but never sends prose to a model/API or executes a writing pass. |

Provider support does not activate every discovered skill. Selection follows project intent,
explicit overrides, and safe sync planning. Unknown or executable provider requirements are
reported rather than run.
