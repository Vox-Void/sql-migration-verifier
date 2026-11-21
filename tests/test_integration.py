import pytest

from migration_verifier.engines import source_connection
from migration_verifier.policy import CASE_IDS, load_case
from migration_verifier.reports import challenge, stable_record
from migration_verifier.verify import Runtime, verify

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def runtime():
    with Runtime() as runtime:
        yield runtime


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_reviewed_reference_meets_all_independent_oracles(runtime, case_id):
    case = load_case(case_id)
    outcome = verify(runtime, case, "baseline", initial_sql=case.reference)
    assert outcome["verdict"] == "VERIFIED", (outcome["reason_code"], outcome["target_attempts"])
    assert outcome["target_executions"] == 3


@pytest.mark.parametrize("identifier", [f"C{number:02}" for number in range(1, 9)])
def test_known_bad_candidate_is_never_approved(runtime, identifier):
    outcome = challenge(runtime, identifier)
    assert outcome["verdict"] == outcome["expected_verdict"], (
        outcome["reason_code"],
        outcome["target_attempts"],
    )
    if identifier in {"C05", "C06", "C08"}:
        assert outcome["target_executions"] == 0
    if identifier == "C07":
        assert outcome["reason_code"] == "ROW_LIMIT", outcome["target_attempts"]
        assert not outcome["target_attempts"][0]["complete"]
    if identifier == "C01":
        comparison = outcome["target_attempts"][0]["comparison"]
        assert comparison["missing"] == comparison["extra"] == 1
        assert comparison["actual_rows"] == comparison["expected_rows"] == 3


def test_recorded_local_day_repair_is_frozen_across_held_out_fixtures(runtime):
    broken = challenge(runtime, "C03")["candidate_sql"]
    case = load_case("Q03")
    first = verify(runtime, case, "recorded", initial_sql=broken)
    repeat = verify(runtime, case, "recorded", initial_sql=broken)
    assert first["verdict"] == "VERIFIED", first
    assert len(first["repairs"]) == 1
    assert first["baseline_sql_hash"] != first["final_sql_hash"]
    assert {item["sql_hash"] for item in first["target_attempts"][1:]} == {first["final_sql_hash"]}
    assert stable_record(first) == stable_record(repeat)


def test_source_role_is_restricted(runtime):
    with source_connection(runtime.port) as connection:
        settings = connection.execute("SHOW transaction_read_only").fetchone()
        temp_grant = connection.execute(
            "SELECT has_database_privilege(current_user, current_database(), 'TEMPORARY')"
        ).fetchone()
        assert settings == ("on",)
        assert temp_grant == (False,)
