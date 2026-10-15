import pytest

from app.schemas.email import Action, AgentAnalysis, Category, NotificationStatus, Priority
from app.services.notification_rules import decide, review_decision


def make_analysis(
    category: Category = Category.OTHER,
    priority: Priority = Priority.MEDIUM,
    needs_response: bool = False,
    confidence: float = 0.9,
) -> AgentAnalysis:
    return AgentAnalysis(
        category=category,
        priority=priority,
        summary="summary",
        needs_response=needs_response,
        confidence=confidence,
        reason="reason",
    )


@pytest.mark.parametrize(
    ("analysis", "expected_action", "expected_rule"),
    [
        (make_analysis(Category.CAREER, Priority.HIGH, confidence=0.3), Action.REVIEW, "low_confidence"),
        (make_analysis(Category.CAREER, Priority.HIGH, True), Action.NOTIFY, "high_priority"),
        (make_analysis(Category.PERSONAL, Priority.HIGH), Action.NOTIFY, "high_priority"),
        (make_analysis(Category.PROMOTION, Priority.HIGH), Action.IGNORE, "promotion"),
        (make_analysis(Category.CAREER, Priority.MEDIUM, True), Action.NOTIFY, "important_needs_response"),
        (make_analysis(Category.FINANCE, Priority.MEDIUM, True), Action.NOTIFY, "important_needs_response"),
        (make_analysis(Category.FINANCE, Priority.MEDIUM, False), Action.IGNORE, "default"),
        (make_analysis(Category.EDUCATION, Priority.MEDIUM, True), Action.IGNORE, "default"),
        (make_analysis(Category.PROMOTION, Priority.LOW), Action.IGNORE, "promotion"),
        (make_analysis(Category.CAREER, Priority.LOW, True), Action.IGNORE, "low_priority"),
    ],
)
def test_decide_applies_first_matching_rule(analysis, expected_action, expected_rule):
    decision = decide(analysis, min_confidence=0.6)

    assert decision.action == expected_action
    assert decision.rule == expected_rule


def test_notify_sets_notification_pending():
    decision = decide(make_analysis(Category.CAREER, Priority.HIGH), min_confidence=0.6)

    assert decision.notification_status == NotificationStatus.PENDING


@pytest.mark.parametrize("action_analysis", [make_analysis(Category.PROMOTION, Priority.LOW)])
def test_non_notify_does_not_require_notification(action_analysis):
    decision = decide(action_analysis, min_confidence=0.6)

    assert decision.notification_status == NotificationStatus.NOT_REQUIRED


def test_confidence_exactly_at_threshold_is_trusted():
    decision = decide(make_analysis(Category.CAREER, Priority.HIGH, confidence=0.6), min_confidence=0.6)

    assert decision.action == Action.NOTIFY


def test_review_decision_for_invalid_output():
    decision = review_decision()

    assert decision.action == Action.REVIEW
    assert decision.notification_status == NotificationStatus.NOT_REQUIRED
    assert decision.rule == "invalid_model_output"
