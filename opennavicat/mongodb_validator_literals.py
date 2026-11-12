"""Bounded BSON data literals for validator Query and raw-snippet editors.

No JavaScript is executed. The existing command grammar is left unchanged;
only this subclass maps ordinary validator number tokens to BSON Double.
"""
from __future__ import annotations

import math

from .database import DatabaseError
from .mongodb_session import _MongoLiteralParser
from .mongodb_validator import ValidatorDraft, ValidatorLimits


MAX_LITERAL_BYTES = 1024 * 1024
MAX_LITERAL_DEPTH = 64
MAX_LITERAL_NODES = 100000


class _ValidatorLiteralParser(_MongoLiteralParser):
    _INTEGER_CONTROLS = frozenset({'Int32', 'NumberInt', 'Long', 'NumberLong',
                                  'Timestamp', 'BinData', 'createFromBase64', 'Date'})

    def __init__(self, text):
        super().__init__(text)
        self._last_identifier = ''
        self._control_depths = []

    def identifier(self):
        self._last_identifier = super().identifier()
        return self._last_identifier

    def arguments(self, depth):
        constructor = self._last_identifier
        controlled = constructor in self._INTEGER_CONTROLS
        if controlled:
            self._control_depths.append(depth + 1)
        try:
            values = super().arguments(depth)
        finally:
            if controlled:
                self._control_depths.pop()

        def integer(value):
            # Constructor control numbers are not ordinary stored data. This
            # keeps Int32(1), Timestamp(1, 2), BinData(128, ...), etc. typed.
            return int(value) if type(value) is float and math.isfinite(value) and value.is_integer() else value

        if controlled:
            values = [integer(value) for value in values]
        if constructor == 'Timestamp' and len(values) == 1 and isinstance(values[0], dict):
            values[0] = {key: integer(value) for key, value in values[0].items()}
        return values

    def value(self, depth=0):
        if depth > MAX_LITERAL_DEPTH or self.nodes >= MAX_LITERAL_NODES:
            raise DatabaseError('The validator literal exceeds its depth or node bound')
        token = self.peek()
        result = super().value(depth)
        if (type(result) is int and token and (token.isdigit() or token in '+-.')
                and depth not in self._control_depths):
            return float(result)
        return result


def parse_validator_literal(text):
    """Parse exactly one nonblank BSON literal; literal null returns None.

    Callers distinguish omitted snippets by checking their blank text before
    calling this function. Query and numeric helpers provide omission handling.
    """
    parser = None
    try:
        if not isinstance(text, str):
            raise DatabaseError('Validator literals must be text')
        if len(text.encode('utf-8')) > MAX_LITERAL_BYTES:
            raise DatabaseError('Validator literals are limited to 1 MiB')
        if not text.strip():
            raise DatabaseError('Enter a BSON literal; an empty editor is not literal null')
        parser = _ValidatorLiteralParser(text)
        value = parser.value()
        parser.skip()
        if parser.pos != len(text):
            raise DatabaseError('Only one BSON literal is accepted; no expressions or trailing statements')
        # Explicit constructors such as EJSON.parse can contain a complete
        # subtree. Check that subtree and its exact BSON, too, without EJSON
        # normalization or a connection, including typed Code/DBRef children.
        ValidatorDraft({'value': value}, limits=ValidatorLimits(
            max_bytes=MAX_LITERAL_BYTES, max_depth=MAX_LITERAL_DEPTH,
            max_nodes=MAX_LITERAL_NODES))
        return value
    except Exception as error:
        if parser is not None:
            position = min(parser.pos, len(text))
            line = text.count('\n', 0, position) + 1
            column = position - text.rfind('\n', 0, position)
            # Location and generic grammar messages are safe. Native BSON
            # exceptions can echo literal values, so their repr/causes stay out.
            detail = str(error) if isinstance(error, DatabaseError) else 'Invalid bounded BSON literal'
            raise DatabaseError(f'Validator literal at line {line}, column {column}: {detail}') from None
        if isinstance(error, DatabaseError):
            raise DatabaseError(str(error)) from None
        raise DatabaseError('Invalid validator BSON literal') from None


def parse_validator_query(text):
    """A complete query-validator object, or None for omitted blank input."""
    if isinstance(text, str) and not text.strip():
        return None
    value = parse_validator_literal(text)
    if not isinstance(value, dict):
        raise DatabaseError('A Query validator must be one BSON object')
    return value


def parse_numeric_constraint(text):
    """New numeric-control input becomes finite BSON Double; blank omits it."""
    if isinstance(text, str) and not text.strip():
        return None
    value = parse_validator_literal(text)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DatabaseError('A numeric validator constraint requires a finite number')
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise DatabaseError('A numeric validator constraint requires a finite Double') from None
    if not math.isfinite(number):
        raise DatabaseError('A numeric validator constraint requires a finite Double')
    return number
