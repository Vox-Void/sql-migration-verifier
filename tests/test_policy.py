import pytest
from sqlglot import exp
from sqlglot.errors import ErrorLevel, UnsupportedError

from migration_verifier.compare import Result, compare
from migration_verifier.oracle import expected, reference
from migration_verifier.policy import CASE_IDS, PolicyError, gate, load_case, translate


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_definitions_agree_with_independent_base_answers(case_id):
    case = load_case(case_id)
    gate(case.reference, case, "spark")
    gate(translate(gate(case.source, case, "postgres")), case, "spark")
    assert compare(
        Result(case.contract.shape, expected(case_id, "repair")),
        Result(case.contract.shape, reference(case_id, "repair")),
        case.contract,
    )["matched"]


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1; SELECT 2",
        "SELECT random()",
        "SELECT 1 FROM merchants",
        "SELECT 1 FROM pg_catalog.pg_roles",
        "WITH outside AS (SELECT 1 FROM merchants) SELECT 1 FROM outside",
        "SELECT 1 WHERE EXISTS (SELECT 1 FROM captures LIMIT 1)",
    ],
)
def test_target_policy_rejects_out_of_scope_sql(query):
    with pytest.raises(PolicyError):
        gate(query, load_case("Q01"), "spark")


def test_unsupported_translation_fails_before_returning_sql(monkeypatch):
    statement = gate(load_case("Q01").source, load_case("Q01"), "postgres")

    def unsupported(_stmt, **kwargs):
        assert kwargs["unsupported_level"] == ErrorLevel.RAISE
        raise UnsupportedError("Unsupported expression")

    monkeypatch.setattr(exp.Select, "sql", unsupported)
    with pytest.raises(PolicyError, match="TRANSLATION_UNSUPPORTED"):
        translate(statement)
