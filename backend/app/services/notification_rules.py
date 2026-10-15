"""Deterministic business rules that turn an LLM analysis into the final action.

The LLM only classifies; this module decides. Rules are evaluated in order and the
first match wins, so put more specific / safety rules first.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.schemas.email import Action, AgentAnalysis, Category, NotificationStatus, Priority

IMPORTANT_CATEGORIES = frozenset({Category.CAREER, Category.FINANCE})


@dataclass(frozen=True)
class Decision:
    action: Action
    notification_status: NotificationStatus
    rule: str


@dataclass(frozen=True)
class Rule:
    name: str
    matches: Callable[[AgentAnalysis, float], bool]
    action: Action


RULES: tuple[Rule, ...] = (
    Rule("low_confidence", lambda a, min_conf: a.confidence < min_conf, Action.REVIEW),
    # Promotions never notify, even if the model was fooled by "URGENT!!!" marketing copy.
    Rule("promotion", lambda a, _: a.category == Category.PROMOTION, Action.IGNORE),
    Rule("high_priority", lambda a, _: a.priority == Priority.HIGH, Action.NOTIFY),
    Rule(
        "important_needs_response",
        lambda a, _: a.category in IMPORTANT_CATEGORIES
        and a.priority == Priority.MEDIUM
        and a.needs_response,
        Action.NOTIFY,
    ),
    Rule("low_priority", lambda a, _: a.priority == Priority.LOW, Action.IGNORE),
)

DEFAULT_RULE = Rule("default", lambda a, _: True, Action.IGNORE)


def _to_decision(rule: Rule) -> Decision:
    notification_status = (
        NotificationStatus.PENDING if rule.action == Action.NOTIFY else NotificationStatus.NOT_REQUIRED
    )
    return Decision(action=rule.action, notification_status=notification_status, rule=rule.name)


def decide(analysis: AgentAnalysis, *, min_confidence: float) -> Decision:
    rule = next((r for r in RULES if r.matches(analysis, min_confidence)), DEFAULT_RULE)
    return _to_decision(rule)


def review_decision() -> Decision:
    """Decision used when the model never produced a valid analysis."""
    return Decision(
        action=Action.REVIEW,
        notification_status=NotificationStatus.NOT_REQUIRED,
        rule="invalid_model_output",
    )
