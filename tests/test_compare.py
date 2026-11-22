from decimal import Decimal

import pytest

from migration_verifier.compare import Contract, Field, Result, ShapeError, compare, normalize


def test_equal_distinct_rows_counts_and_sum_can_hide_the_wrong_multiplicity():
    shape = Contract((Field("source_ref", "text"), Field("amount", "decimal(18,2)")), "bag")
    expected = Result(shape.shape, [["A", "100.10"], ["A", "100.10"], ["B", "100.10"]])
    mutated = Result(shape.shape, [["A", "100.10"], ["B", "100.10"], ["B", "100.10"]])

    def weak_summary(result):
        return (
            len(result.rows),
            set(map(tuple, result.rows)),
            sum(Decimal(row[1]) for row in result.rows),
        )

    assert (
        weak_summary(expected)
        == weak_summary(mutated)
        == (3, {("A", "100.10"), ("B", "100.10")}, Decimal("300.30"))
    )
    report = compare(expected, mutated, shape)
    assert not report["matched"]
    assert report["examples"]["missing"] == [((("text", "A"), ("decimal(18,2)", "100.10")), 1)]
    assert report["examples"]["extra"] == [((("text", "B"), ("decimal(18,2)", "100.10")), 1)]


@pytest.mark.parametrize("value", [1.1, "1.1001", "10000000000000000.00", "NaN", "-Infinity"])
def test_amount_conversion_rejects_floats_and_rounding(value):
    with pytest.raises(ShapeError):
        normalize(value, Field("amount", "decimal(18,2)"))


def test_null_empty_and_literal_null_are_distinct():
    field = Field("reference", "text", True)
    assert len({normalize(item, field) for item in (None, "", "NULL")}) == 3


def test_instant_timezone_and_precision():
    field = Field("occurred_at", "instant")
    assert normalize("2026-01-07T22:00:00Z", field) == normalize("2026-01-08T00:00:00+02:00", field)
    assert normalize("2026-01-07T21:59:59.999999Z", field) != normalize(
        "2026-01-07T22:00:00Z", field
    )
    with pytest.raises(ShapeError):
        normalize("2026-01-07T22:00:00", field)
    with pytest.raises(ShapeError, match="exceeds microsecond precision"):
        normalize("2026-01-07T21:59:59.9999999Z", field)
    with pytest.raises(ShapeError):
        normalize("0001-01-01T00:00:00+01:00", field)


def test_empty_result_preserves_the_schema():
    shape = Contract((Field("net_amount", "decimal(18,2)"),), "bag")
    wrong = compare(Result(shape.shape, []), Result((("net_amount", "float"),), []), shape)
    assert not wrong["matched"]
    assert wrong["shape_error"]


def test_reversed_order_and_duplicate_keys_are_both_mismatches():
    ordered = Contract((Field("capture_id", "text"),), "ordered")
    expected = Result(ordered.shape, [["C1"], ["C2"]])
    assert compare(expected, Result(ordered.shape, [["C2"], ["C1"]]), ordered)["changed"] == 2
    keyed = Contract(ordered.fields, "keyed", keys=("capture_id",))
    duplicate = compare(expected, Result(ordered.shape, [["C1"], ["C1"]]), keyed)
    assert "repeated" in duplicate["shape_error"]
