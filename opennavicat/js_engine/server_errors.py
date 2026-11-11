"""Bounded data from a real MongoDB error reply, separate from lost transport IO."""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerFailure:
    code: int
    code_name: str
    document: dict = field(repr=False)
    concern_uncertain: bool = False


def server_failure(error):
    """Find a driver error through adapter wrappers; never parse message text."""
    seen=set()
    for _ in range(8):
        if error is None or id(error) in seen:return None
        seen.add(id(error))
        details=getattr(error,'details',None)
        code=getattr(error,'code',None)
        if isinstance(details,Mapping) and details.get('ok')==0 and type(code)is int and 0<=code<2**31:
            # Keep VM-only errors from importing a database client package.
            from pymongo.errors import OperationFailure
            if isinstance(error,OperationFailure):
                name=details.get('codeName','')
                if not isinstance(name,str) or len(name)>128 or not name.isascii() or not name.isalnum():name=''
                return ServerFailure(code,name,deepcopy(dict(details)),
                    'WriteConcern'in name or isinstance(details.get('writeConcernError'),Mapping))
        error=error.__cause__ if error.__cause__ is not None else error.__context__
    return None
