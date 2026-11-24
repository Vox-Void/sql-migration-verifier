from unittest.mock import Mock

import pytest

from migration_verifier import verify
from migration_verifier.compare import Result
from migration_verifier.fixtures import digest
from migration_verifier.policy import load_case
from migration_verifier.repair import Suggestion


@pytest.mark.parametrize(
    "verdicts,selected",
    [
        (("MISMATCH", "MISMATCH", "REVIEW_REQUIRED"), 1),
        (("REVIEW_REQUIRED", "REVIEW_REQUIRED", "REVIEW_REQUIRED"), 2),
    ],
)
def test_failed_report_uses_the_candidate_that_determined_the_verdict(
    monkeypatch: pytest.MonkeyPatch, verdicts: tuple[str, ...], selected: int
) -> None:
    case = load_case("Q03")
    runtime = Mock(executions=0)
    candidates = ("SELECT 1 AS baseline", "SELECT 2 AS first_repair", "SELECT 3 AS second_repair")
    attempts = [
        {
            "fixture": case.fixtures[0],
            "sql_hash": digest(candidate),
            "verdict": verdict,
            "reason": f"attempt {index}",
            "complete": verdict == "MISMATCH",
        }
        for index, (candidate, verdict) in enumerate(zip(candidates, verdicts, strict=True))
    ]
    monkeypatch.setattr(
        verify,
        "source_check",
        Mock(return_value=(Result(case.contract.shape, []), {"matched": True})),
    )
    monkeypatch.setattr(verify, "check_target", Mock(side_effect=attempts))
    monkeypatch.setattr(
        verify,
        "propose",
        Mock(side_effect=[Suggestion(sql, "", [], None) for sql in candidates[1:]]),
    )

    outcome = verify.verify(runtime, case, "recorded", initial_sql=candidates[0])

    assert outcome["verdict"] == verdicts[selected]
    assert outcome["reason_code"] == attempts[selected]["reason"]
    assert outcome["candidate_sql"] == candidates[selected]
    assert f"+{candidates[selected]}" in outcome["sql_diff"]
    assert outcome["target_attempts"] == attempts
    assert outcome["baseline_sql_hash"] == digest(candidates[0])
    assert outcome["final_sql_hash"] is None


def test_source_mismatch_stops_before_target_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    case = load_case("Q03")
    runtime = Mock(executions=0)
    target_check = Mock()
    monkeypatch.setattr(
        verify,
        "source_check",
        Mock(return_value=(Result(case.contract.shape, []), {"matched": False})),
    )
    monkeypatch.setattr(verify, "check_target", target_check)

    outcome = verify.verify(runtime, case, "recorded", initial_sql="SELECT 1 AS baseline")

    assert outcome["verdict"] == "ERROR"
    assert outcome["reason_code"] == "SOURCE_ORACLE_DIFF"
    assert outcome["candidate_sql"] is None
    assert outcome["baseline_sql_hash"] is None
    assert outcome["final_sql_hash"] is None
    assert outcome["target_attempts"] == []
    target_check.assert_not_called()
