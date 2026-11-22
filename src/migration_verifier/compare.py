"""Compare complete, typed results without losing nulls, order, or multiplicity."""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, DecimalException, localcontext
from typing import Any

Atom = tuple[str, str | int | bool | None]
Row = tuple[Atom, ...]


class ShapeError(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    nullable: bool = False


@dataclass(frozen=True)
class Result:
    columns: tuple[tuple[str, str], ...]
    rows: list[list[Any]]


@dataclass(frozen=True)
class Order:
    field: str
    descending: bool = False
    nulls_first: bool = False


@dataclass(frozen=True)
class Contract:
    fields: tuple[Field, ...]
    mode: str
    keys: tuple[str, ...] = ()
    order: tuple[Order, ...] = ()

    @property
    def shape(self) -> tuple[tuple[str, str], ...]:
        return tuple((field.name, field.kind) for field in self.fields)


def normalize(raw: Any, field: Field) -> Atom:
    # Represent a database null with its own tag.
    if raw is None:
        if not field.nullable:
            raise ShapeError(f"{field.name} cannot be null")
        return ("null", None)
    kind = field.kind
    if kind == "text" and isinstance(raw, str):
        return (kind, raw)
    if (kind == "integer" and type(raw) is int) or (kind == "boolean" and type(raw) is bool):
        return (kind, raw)
    if kind == "instant" and isinstance(raw, str) and re.search(r"[.,]\d{7,}", raw):
        raise ShapeError(f"{field.name} exceeds microsecond precision")
    try:
        if kind == "date":
            value = date.fromisoformat(raw) if isinstance(raw, str) else raw
            if type(value) is date:
                return (kind, value.isoformat())
        if kind == "instant":
            # Convert timestamps with an offset into UTC strings with microseconds.
            value = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
            if isinstance(value, datetime) and value.utcoffset() is not None:
                return (kind, value.astimezone(UTC).isoformat(timespec="microseconds"))
        decimal = re.fullmatch(r"decimal\((\d+),(\d+)\)", kind)
        if decimal and isinstance(raw, (str, Decimal)):
            precision, scale = map(int, decimal.groups())
            amount = Decimal(raw)
            # Apply the declared scale and check that the amount stayed exact.
            with localcontext() as context:
                context.prec = precision + 2
                normalized = amount.quantize(Decimal(1).scaleb(-scale))
            if (
                amount.is_finite()
                and normalized == amount
                and normalized.adjusted() < precision - scale
            ):
                return (kind, format(normalized if normalized else abs(normalized), "f"))
    except (ValueError, DecimalException, OverflowError) as error:
        raise ShapeError(f"{field.name} is invalid for {kind}") from error
    raise ShapeError(f"{field.name} is invalid for {kind}")


def normalized(result: Result, contract: Contract) -> list[Row]:
    # Check result columns even when the query returns no rows.
    if result.columns != contract.shape:
        raise ShapeError("The ordered columns or logical types differ from the contract")
    if any(len(row) != len(contract.fields) for row in result.rows):
        raise ShapeError("A result row has the wrong number of fields")
    return [
        tuple(normalize(value, field) for value, field in zip(row, contract.fields, strict=True))
        for row in result.rows
    ]


def keyed(rows: list[Row], indexes: tuple[int, ...]) -> dict[Row, Row]:
    # Index each row by its contract key and reject repeated or null keys.
    indexed = {}
    for row in rows:
        key = tuple(row[index] for index in indexes)
        if any(kind == "null" for kind, _ in key) or key in indexed:
            raise ShapeError("A contract key is null or repeated")
        indexed[key] = row
    return indexed


def compare(expected: Result, actual: Result, contract: Contract) -> dict[str, Any]:
    report: dict[str, Any] = {
        "mode": contract.mode,
        "expected_rows": len(expected.rows),
        "actual_rows": len(actual.rows),
    }
    try:
        left = normalized(expected, contract)
        right = normalized(actual, contract)
        if contract.mode == "keyed":
            # Compare column values for keys found on both sides.
            labels = [field.name for field in contract.fields]
            indexes = tuple(labels.index(name) for name in contract.keys)
            expected_keys, actual_keys = keyed(left, indexes), keyed(right, indexes)
            missing = sorted(expected_keys.keys() - actual_keys.keys(), key=repr)
            extra = sorted(actual_keys.keys() - expected_keys.keys(), key=repr)
            changed = []
            for key in sorted(expected_keys.keys() & actual_keys.keys(), key=repr):
                fields = {
                    label: {"expected": before, "actual": after}
                    for label, before, after in zip(
                        labels, expected_keys[key], actual_keys[key], strict=True
                    )
                    if before != after
                }
                if fields:
                    changed.append({"key": key, "fields": fields})
            report.update(
                matched=not (missing or extra or changed),
                missing=len(missing),
                extra=len(extra),
                changed=len(changed),
                examples={"missing": missing[:10], "extra": extra[:10], "changed": changed[:10]},
            )
        elif contract.mode == "bag":
            # Count copies of each complete row on both sides.
            expected_rows, actual_rows = Counter(left), Counter(right)
            missing_rows, extra_rows = expected_rows - actual_rows, actual_rows - expected_rows
            report.update(
                matched=not (missing_rows or extra_rows),
                missing=sum(missing_rows.values()),
                extra=sum(extra_rows.values()),
                examples={
                    "missing": sorted(missing_rows.items(), key=repr)[:10],
                    "extra": sorted(extra_rows.items(), key=repr)[:10],
                },
            )
        elif contract.mode == "ordered":
            # Compare rows at matching positions and count unmatched rows at the end.
            differences = [
                (index, before, after)
                for index, (before, after) in enumerate(zip(left, right, strict=False))
                if before != after
            ]
            report.update(
                matched=left == right,
                changed=len(differences),
                missing=max(0, len(left) - len(right)),
                extra=max(0, len(right) - len(left)),
                examples={"changed": differences[:10]},
            )
        else:
            raise ShapeError(f"Unknown comparison mode {contract.mode}")
    except ShapeError as problem:
        report.update(
            matched=False,
            shape_error=str(problem),
            expected_schema=contract.shape,
            actual_schema=actual.columns,
        )
    return report
