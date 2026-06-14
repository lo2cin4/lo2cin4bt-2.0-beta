# Backtest Unknown Unknowns Register

Last updated: 2026-04-05

## Resolved This Round
- Runner entrypoint encoding noise was reduced by rewriting `autorunner/BacktestRunner_autorunner.py` in clean UTF-8 source text.
- A compile boundary now exists: semantic strategy contract can be compiled to `execution_plan.json` deterministically.
- Unsafe silent fallback was removed for `strategy_mode=semantic` without legacy adapter payload (now hard-fail).

## Open Risks (Not Fully Resolved Yet)
- NodeIR/native execution is now the supported semantic path; remaining risk is adapter cleanup around older config normalization.
- Wide codebase still contains historical encoding artifacts in comments/docstrings; only critical entry files were cleaned this round.
- Strict `dtype/unit` enforcement is not fully implemented in compiler/runtime (currently marked unknown in field catalog).
- Multi-source namespace policy is not fully wired into runtime data loading (`source_id` still mostly compile-time metadata).
- `execution_plan` lifecycle policy is partially implemented; retention and cleanup rules are still missing.

## Priority Mitigations
1. Continue hardening the NodeIR/native runtime path and remove old config-normalization adapters where no longer needed.
2. Add strict field contract checks (`dtype`, `unit`, `frequency`, `timezone`) before execution.
3. Add global UTF-8 normalization pass for key runtime modules and top-level docs.
4. Add execution plan retention policy (`keep_last_n`, explicit cleanup command, report traceability).
