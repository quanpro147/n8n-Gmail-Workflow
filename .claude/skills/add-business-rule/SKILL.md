---
name: add-business-rule
description: Add or change a notification/decision rule (notify, ignore, review) for classified emails. Use when the user wants different emails to trigger or skip Telegram notifications, or wants to tune confidence thresholds.
---

# Add or change a business rule

Rules live in `backend/app/services/notification_rules.py` as the ordered tuple `RULES`. The first matching rule wins, and `DEFAULT_RULE` (ignore) is the fallback.

Current order and why:
1. `low_confidence` → review. Safety first: never act on an unsure analysis.
2. `promotion` → ignore. Comes before `high_priority` so marketing copy like "URGENT" can't trigger a notification.
3. `high_priority` → notify
4. `important_needs_response` (career/finance, medium, needs_response) → notify
5. `low_priority` → ignore

## Steps
1. **Test first.** Add a row to the parametrized table in `backend/tests/test_rules.py` with the analysis, the expected action and the expected rule name. Run it and watch it fail.
2. Add a `Rule(name, matches, action)` at the right position. `matches(analysis, min_confidence) -> bool` must be pure: no I/O, no LLM.
3. If the rule needs a new threshold, add it to `Settings` (`backend/app/core/config.py`) and `backend/.env.example`, then pass it through `decide()` the way `min_confidence` is passed.
4. If the rule needs a new `AgentAnalysis` field, add it in `backend/app/schemas/email.py` and describe it in `SYSTEM_INSTRUCTION` (`agent_service.py`). Don't add a column unless it should be stored, and if you do, add a migration too.
5. Run `pytest tests/test_rules.py tests/test_processing.py`.

## Invariants
- The LLM never chooses the action. Only these rules do.
- `notify` ⇒ `notification_status=PENDING`. Every other action ⇒ `NOT_REQUIRED` (handled in `_to_decision`).
- The `rule` name is stored in `email_records.decision_rule` for auditing, so keep names stable.
- Adding a new `Action` value changes the API contract with n8n. Update `n8n/README.md` too.
