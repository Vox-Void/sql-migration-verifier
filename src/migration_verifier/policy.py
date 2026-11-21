"""Reject any SQL shape the migration verifier does not explicitly support."""

import json
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError, UnsupportedError
from sqlglot.optimizer.scope import Scope, traverse_scope

from migration_verifier.compare import Contract, Field, Order
from migration_verifier.engines import ROOT
from migration_verifier.fixtures import FIXTURES, SCHEMAS

CASE_IDS = tuple(f"Q{index:02}" for index in range(1, 13))
# These are the SQLGlot node names accepted anywhere in a query.
SHAPES = frozenset(
    [
        "Add",
        "Alias",
        "And",
        "Anonymous",
        "AtTimeZone",
        "Between",
        "Boolean",
        "Case",
        "Cast",
        "Coalesce",
        "Column",
        "Count",
        "CTE",
        "DataType",
        "DataTypeParam",
        "Distinct",
        "Div",
        "EQ",
        "Exists",
        "From",
        "GT",
        "Group",
        "GTE",
        "Having",
        "Identifier",
        "If",
        "In",
        "Is",
        "Join",
        "Limit",
        "Literal",
        "LT",
        "LTE",
        "Max",
        "Min",
        "Mul",
        "NEQ",
        "Neg",
        "Not",
        "Null",
        "NullSafeEQ",
        "Nullif",
        "Offset",
        "Or",
        "Order",
        "Ordered",
        "Paren",
        "RowNumber",
        "Select",
        "Star",
        "Sub",
        "Subquery",
        "Sum",
        "Table",
        "TableAlias",
        "TryCast",
        "Union",
        "Intersect",
        "Except",
        "Where",
        "Window",
        "With",
    ]
)
FUNCTIONS = frozenset(
    [
        "AND",
        "CAST",
        "COALESCE",
        "COUNT",
        "EXISTS",
        "IF",
        "MAX",
        "MIN",
        "NULLIF",
        "OR",
        "ROW_NUMBER",
        "SUM",
        "TRY_CAST",
        "FROM_UTC_TIMESTAMP",
    ]
)
TYPES = frozenset(
    {
        "BIGINT",
        "BOOLEAN",
        "DATE",
        "DECIMAL",
        "INT",
        "SMALLINT",
        "TEXT",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "VARCHAR",
    }
)


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Case:
    case_id: str
    purpose: str
    relations: tuple[str, ...]
    functions: frozenset[str]
    fixtures: tuple[str, ...]
    row_cap: int
    contract: Contract
    source: str
    reference: str
    contract_bytes: bytes


def load_case(case_id: str) -> Case:
    if case_id not in CASE_IDS:
        raise ValueError(f"Unknown case ID {case_id!r}")
    directory = ROOT / "cases" / case_id
    serialized = (directory / "contract.json").read_bytes()
    raw = json.loads(serialized)
    # Build the expected column, key and sort definitions from the case file.
    contract = Contract(
        tuple(Field(*field) for field in raw["fields"]),
        raw["mode"],
        tuple(raw.get("keys", [])),
        tuple(Order(*order) for order in raw.get("order", [])),
    )
    names = {field.name for field in contract.fields}
    if (
        set(raw["relations"]) - SCHEMAS.keys()
        or set(raw["functions"]) - FUNCTIONS
        or tuple(raw["fixtures"]) != FIXTURES
        or not 1 <= raw["row_cap"] <= 10000
        or not names
        or len(names) != len(contract.fields)
        or contract.mode not in {"keyed", "bag", "ordered"}
        or (
            contract.mode == "keyed"
            and not 0 < len(contract.keys) == len(set(contract.keys) & names)
        )
        or (
            contract.mode == "ordered"
            and not 0 < len(contract.order) == len({item.field for item in contract.order} & names)
        )
    ):
        raise ValueError(f"Invalid contract for {case_id}")
    return Case(
        case_id,
        raw["purpose"],
        tuple(raw["relations"]),
        frozenset(raw["functions"]),
        tuple(raw["fixtures"]),
        raw["row_cap"],
        contract,
        (directory / "source.sql").read_text(encoding="utf-8"),
        (directory / "reference.sql").read_text(encoding="utf-8"),
        serialized,
    )


def gate(query: str, case: Case, dialect: str) -> exp.Expression:
    if len(query.encode()) > 12_000:
        raise PolicyError("SQL_LIMIT")
    try:
        expressions = sqlglot.parse(query, read=dialect, error_level=ErrorLevel.IMMEDIATE)
    except (ParseError, RecursionError) as error:
        raise PolicyError("INVALID_SQL") from error
    if len(expressions) != 1 or not isinstance(
        expressions[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
    ):
        raise PolicyError("QUERY_ONLY")
    parsed = expressions[0]
    # Inspect each parsed node, including expressions inside subqueries.
    for node in parsed.walk():
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
            if name not in case.functions:
                raise PolicyError("FUNCTION_NOT_ALLOWED")
        if type(node).__name__ not in SHAPES:
            raise PolicyError("UNSUPPORTED_SQL_SHAPE")
        if isinstance(node, exp.DataType) and getattr(node.this, "name", "") not in TYPES:
            raise PolicyError("TYPE_NOT_ALLOWED")
        if isinstance(node, exp.Select) and len(node.expressions) > 32:
            raise PolicyError("PROJECTION_LIMIT")
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            raise PolicyError("STAR_PROJECTION")
        if isinstance(node, (exp.Limit, exp.Offset)) and (
            node.parent is not parsed or case.contract.mode != "ordered"
        ):
            raise PolicyError("LIMIT_REQUIRES_TOTAL_ORDER")
    # Resolve table names in each scope and skip sources backed by another query scope.
    for scope in traverse_scope(parsed):
        for _, source in scope.selected_sources.values():
            if isinstance(source, Scope):
                continue
            if (
                not isinstance(source, exp.Table)
                or source.catalog
                or source.db
                or source.name not in case.relations
            ):
                raise PolicyError("RELATION_NOT_ALLOWED")
    if case.contract.mode == "ordered":
        # Compare the ordered columns, directions and null placement with the contract.
        order = parsed.args.get("order")
        if not isinstance(order, exp.Order):
            raise PolicyError("TOTAL_ORDER_REQUIRED")
        found = []
        for term in order.expressions:
            if not isinstance(term, exp.Ordered) or not isinstance(term.this, exp.Column):
                raise PolicyError("TOTAL_ORDER_REQUIRED")
            found.append(
                Order(
                    term.this.name, bool(term.args.get("desc")), bool(term.args.get("nulls_first"))
                )
            )
        if tuple(found) != case.contract.order:
            raise PolicyError("TOTAL_ORDER_REQUIRED")
    if re.search(r"\x00", query):
        raise PolicyError("INVALID_SQL")
    return parsed


def translate(checked: exp.Expression) -> str:
    # Report a translation error when SQLGlot flags an unsupported Spark construct.
    try:
        return checked.sql(dialect="spark", unsupported_level=ErrorLevel.RAISE, pretty=True)
    except UnsupportedError as error:
        raise PolicyError("TRANSLATION_UNSUPPORTED") from error
