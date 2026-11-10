"""Strict BSON-tagged IPC codec; no Extended JSON wrapper interpretation."""
from __future__ import annotations
import base64, math, re
from datetime import datetime, timezone
from enum import Enum
from bson import Binary, Code, DBRef, Decimal128, Int64, MaxKey, MinKey, ObjectId, Regex, Timestamp
from bson.datetime_ms import DatetimeMS

class JavaScriptUndefined(Enum):
    VALUE = "undefined"

JS_UNDEFINED = JavaScriptUndefined.VALUE

def integer(value,lo,hi):
    if not isinstance(value,int) or isinstance(value,bool) or not lo<=value<=hi:raise ValueError('Integer range')
    return value

def decode(t,depth=0,allow_big=False):
    if depth>80 or not isinstance(t,list) or not t or not isinstance(t[0],str):raise ValueError('Invalid typed data tree')
    tag=t[0];a=t[1] if len(t)>1 else None
    def arity(n):
        if len(t)!=n:raise ValueError('Invalid typed data arity')
    if tag=='null':arity(1);return None
    if tag=='js-undefined':
        arity(1)
        if not allow_big:raise ValueError('Undefined is not a BSON value')
        return JS_UNDEFINED
    if tag=='bool':
        arity(2)
        if type(a)is not bool:raise ValueError('Boolean required')
        return a
    if tag=='string':
        arity(2)
        if not isinstance(a,str):raise ValueError('String required')
        return a
    if tag=='int32':arity(2);return integer(a,-2**31,2**31-1)
    if tag in {'int64','date','js-bigint'}:
        arity(2)
        if not isinstance(a,str) or not re.fullmatch(r'-?(0|[1-9][0-9]*)',a):raise ValueError('Exact decimal integer required')
        n=int(a)
        if tag=='js-bigint':
            if not allow_big:raise ValueError('Unbounded integers are not BSON values')
            return n
        integer(n,-2**63,2**63-1)
        return Int64(n) if tag=='int64' else DatetimeMS(n)
    if tag=='double':
        arity(2)
        if not isinstance(a,str):raise ValueError('Exact double spelling required')
        return float(a)
    if tag=='decimal128':arity(2);return Decimal128(a)
    if tag=='objectid':arity(2);return ObjectId(a)
    if tag=='binary':
        arity(3);return Binary(base64.b64decode(a,validate=True),integer(t[2],0,255))
    if tag=='regex':
        arity(3)
        if not isinstance(a,str)or not isinstance(t[2],str)or any(x not in 'ilmsux'for x in t[2]):raise ValueError('Regex shape')
        return Regex(a,t[2])
    if tag=='timestamp':arity(3);return Timestamp(integer(a,0,2**32-1),integer(t[2],0,2**32-1))
    if tag=='minkey':arity(1);return MinKey()
    if tag=='maxkey':arity(1);return MaxKey()
    if tag=='code':arity(3);return Code(a,None if t[2]is None else decode(t[2],depth+1))
    if tag=='dbref':arity(4);return DBRef(a,decode(t[2],depth+1),t[3])
    if tag=='array':
        arity(2)
        if not isinstance(a,list):raise ValueError('Array required')
        return [decode(v,depth+1,allow_big)for v in a]
    if tag=='document':
        arity(2)
        if not isinstance(a,list):raise ValueError('Document field list required')
        out={}
        for pair in a:
            if not isinstance(pair,list)or len(pair)!=2 or not isinstance(pair[0],str)or pair[0]in out:raise ValueError('Invalid or duplicate document field')
            out[pair[0]]=decode(pair[1],depth+1,allow_big)
        return out
    raise ValueError('Unknown typed data tag')

def encode(v):
    if isinstance(v,Code):return ['code',str(v),None if v.scope is None else encode(v.scope)]
    if isinstance(v,DBRef):return ['dbref',v.collection,encode(v.id),v.database]
    if v is None:return ['null']
    if isinstance(v,bool):return ['bool',v]
    if isinstance(v,Int64):return ['int64',str(v)]
    if isinstance(v,int):return ['int32',v] if -2**31<=v<2**31 else ['int64',str(v)]
    if isinstance(v,float):return ['double','NaN' if math.isnan(v)else'Infinity' if v==math.inf else'-Infinity' if v==-math.inf else'-0' if v==0 and math.copysign(1,v)<0 else repr(v)]
    if isinstance(v,str):return ['string',v]
    if isinstance(v,(bytes,Binary)):return ['binary',base64.b64encode(v).decode(),getattr(v,'subtype',0)]
    if isinstance(v,Decimal128):return ['decimal128',str(v)]
    if isinstance(v,ObjectId):return ['objectid',str(v)]
    if isinstance(v,DatetimeMS):return ['date',str(int(v))]
    if isinstance(v,datetime):
        delta=v-datetime(1970,1,1,tzinfo=timezone.utc)
        if delta.microseconds%1000:raise ValueError('BSON millisecond precision')
        return ['date',str(delta.days*86400000+delta.seconds*1000+delta.microseconds//1000)]
    if isinstance(v,Regex):
        flags=''.join(k for k,f in [('i',re.I),('l',re.L),('m',re.M),('s',re.S),('u',re.U),('x',re.X)]if v.flags&f)
        return ['regex',v.pattern,flags]
    if isinstance(v,Timestamp):return ['timestamp',v.time,v.inc]
    if isinstance(v,MinKey):return ['minkey']
    if isinstance(v,MaxKey):return ['maxkey']
    if isinstance(v,list):return ['array',[encode(x)for x in v]]
    if isinstance(v,dict):return ['document',[[k,encode(x)]for k,x in v.items()]]
    raise ValueError('Unsupported BSON host type')

