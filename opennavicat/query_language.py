"""Persistent execution language; historical text never changes parser implicitly."""
LEGACY = 'legacy'
MONGO_JS = 'mongodb-javascript'
LANGUAGES = frozenset({LEGACY, MONGO_JS})


def query_language(value, *, driver, new=False):
    if value is None:
        return MONGO_JS if new and driver == 'mongodb' else LEGACY
    if not isinstance(value, str) or value not in LANGUAGES:
        raise ValueError('Unsupported saved query language; select an execution language before running')
    if value == MONGO_JS and driver != 'mongodb':
        raise ValueError('A MongoDB JavaScript query requires a MongoDB connection')
    return value
