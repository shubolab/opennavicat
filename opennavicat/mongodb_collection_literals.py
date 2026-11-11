"""Bounded, non-evaluating object literals for collection storage editors."""
from __future__ import annotations

from .database import DatabaseError
from .mongodb_collections import CollectionDefinition, validate_definition
from .mongodb_session import _MongoLiteralParser


MAX_STORAGE_TEXT_BYTES = 1024 * 1024


def parse_storage_option(text, option):
    """Return one qualified BSON document, or None for an omitted blank field.

    This reuses the existing data grammar, never the native JavaScript VM.
    Neither expressions, query commands nor trailing statements are evaluated.
    """
    if option not in {'storageEngine', 'indexOptionDefaults'}:
        raise DatabaseError('Unknown collection storage option')
    if not isinstance(text, str):
        raise DatabaseError('Collection storage configuration must be text')
    try:
        if len(text.encode('utf-8')) > MAX_STORAGE_TEXT_BYTES:
            raise DatabaseError('Collection storage configuration is limited to 1 MiB')
        if not text.strip():
            return None
        parser = _MongoLiteralParser(text)
        if parser.peek() != '{':
            raise DatabaseError('Enter one collection storage configuration object')
        value = parser.value()
        parser.skip()
        if parser.pos != len(parser.text) or not isinstance(value, dict):
            raise DatabaseError('Only one collection storage configuration object is accepted')
        # A detached placeholder validates option shape/encoding only. It never
        # becomes a collection name or reaches the database task queue.
        validate_definition(CollectionDefinition('storage_draft', {option: value}))
        storage = value if option == 'storageEngine' else value['storageEngine']
        if type(storage['wiredTiger']['configString']) is not str:
            raise DatabaseError('configString must be a BSON string, not a code constructor')
        return value
    except (UnicodeError, ValueError, OverflowError, RecursionError) as error:
        raise DatabaseError('Invalid collection storage configuration literal') from error
