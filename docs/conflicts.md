# Deterministic conflict handling

V1 groups possible overlap only from local deterministic evidence: normalized skill names,
explicit registry aliases, provider-declared capabilities/tags, and existing overrides. It
does not use description similarity, embeddings, LLMs, remote APIs, or network calls.

Groups are shown by capability rather than as pairwise rows. A user can persist an explicit
owner preference or suppression decision in `.ai/overrides.yaml`. References use normalized
skill IDs; a missing referenced ID is an explicit error.

The default for an unresolved group is **keep all**. No provider is silently suppressed,
including when evidence is unknown or low confidence. Interactive resolution has a default
budget of ten groups; groups beyond that budget remain keep-all. Non-interactive sync fails
closed for unresolved single-owner policy or any other non-deterministic/unsafe state.
