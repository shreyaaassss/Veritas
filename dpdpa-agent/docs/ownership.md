# Module Ownership

> Fill in your name against the module you own before Phase 1 begins.
> One owner per module — they are the decision-maker for that phase's implementation.

| Phase | Module | Owner | Status |
|---|---|---|---|
| 1 | Consent Registry | | Not started |
| 2 | Ingestion Pipeline (log simulator + WebSocket) | | Not started |
| 3 | PII Detection Engine (Presidio + Indian regex) | | Not started |
| 4 | Rule Engine / Policy Decision Point | | Not started |
| 5 | LLM Explainer + Guardrails (Claude API) | | Not started |
| 6 | Evidence Store (SQLite + hash chain) | | Not started |
| 7 | Live Dashboard (React — live feed + audit view) | | Not started |
| — | Pitch / Demo Script / Slides | | Not started |

---

## Ground Rules

- The **schemas in `/schemas/`** are owned by no single person — they are team contracts. Any change requires agreement from all module owners whose phase is affected.
- `docs/scope.md` is similarly a team contract — no unilateral additions to in-scope rules.
- The `Evidence Store` owner (Phase 6) has veto power on anything that would violate the immutability contract defined in `docs/scope.md`.
