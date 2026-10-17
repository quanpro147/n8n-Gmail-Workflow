import json

import pytest

from app.schemas.email import Category, Priority
from eval.dataset import DEFAULT_DATASET, DatasetError, load_dataset


def test_shipped_dataset_loads_and_covers_every_label():
    cases = load_dataset(DEFAULT_DATASET)

    assert len(cases) >= 30
    assert {c.expected.category for c in cases} == set(Category)
    assert {c.expected.priority for c in cases} == set(Priority)
    assert {c.lang for c in cases} == {"en", "vi"}
    assert any("injection" in c.tags for c in cases)
    assert any(c.expects_tool and c.history for c in cases)


def test_history_result_has_the_lookup_tool_shape():
    case = next(c for c in load_dataset() if c.history)

    result = case.history_result()

    assert result["sender"] == case.to_email_in().sender_address
    assert result["count"] == len(case.history)
    assert set(result["records"][0]) == {"category", "priority", "action", "received_at"}


def _line(**overrides) -> str:
    values = {
        "id": "case-1",
        "lang": "en",
        "email": {
            "subject": "s",
            "body": "b",
            "sender": "a@example.com",
            "received_at": "2026-10-01T09:00:00Z",
        },
        "expected": {"category": "other", "priority": "low", "action": "ignore"},
    }
    return json.dumps({**values, **overrides})


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text(_line() + "\n" + _line() + "\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="duplicate id"):
        load_dataset(path)


BAD_SENDER_EMAIL = {
    "subject": "s",
    "body": "b",
    "sender": "no-address",
    "received_at": "2026-10-01T09:00:00Z",
}


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected": {"category": "spam", "priority": "low", "action": "ignore"}},
        {"lang": "fr"},
        {"surprise": True},
        {"email": BAD_SENDER_EMAIL},
    ],
)
def test_invalid_rows_fail_with_line_number(tmp_path, overrides):
    path = tmp_path / "data.jsonl"
    path.write_text(_line(id="ok") + "\n" + _line(**overrides) + "\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="data.jsonl:2"):
        load_dataset(path)


def test_empty_dataset_is_rejected(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text("\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="no cases"):
        load_dataset(path)
