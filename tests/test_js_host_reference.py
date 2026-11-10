"""Synthetic regressions derived from original Navicat18 own-probe wire data.

The original two probe collections were removed after exact ID/marker checks.
These tests accept only the fresh Docker fixture, never that reference endpoint.
"""
from __future__ import annotations

import math
import struct

from bson import BSON, Int64
import pytest

from opennavicat.js_engine.runtime import JavaScriptError
from opennavicat.mongodb_session import BSON_CODEC, RAW_CODEC
from test_js_engine import engine
from test_mongodb_session import mongodb_service, session


@pytest.fixture
def seeded(session):
    value={'_id':'seed','marker':'synthetic-host-reference','i32':1,'nested':{'i32':1},'arr':[1,1.0,Int64(1)]}
    session.connection[session.config.database].docs.insert_one(value)
    return session,BSON.encode(value,codec_options=BSON_CODEC)


def shape(value):
    if isinstance(value,Int64):return 'long'
    if type(value)is int:return 'int'
    if type(value)is float:return 'double'
    if isinstance(value,list):return [shape(v)for v in value]
    if isinstance(value,dict):return {k:shape(v)for k,v in value.items()if k not in {'_id','marker'}}
    return type(value).__name__


FRESH_CASES=[
    ('fresh-fields',"({i32:d.i32,nested_i32:d.nested.i32,arr0:d.arr[0],arr1:d.arr[1],arr2:d.arr[2]})",
        {'i32':'double','nested_i32':'double','arr0':'double','arr1':'double','arr2':'long'}),
    ('fresh-components','({nested:d.nested,arr:d.arr})',{'nested':{'i32':'double'},'arr':['double','double','long']}),
    ('array-slice','({arr:d.arr.slice()})',{'arr':['double','double','long']}),
    ('array-spread','({arr:[...d.arr]})',{'arr':['double','double','long']}),
    ('nested-spread','({nested:{...d.nested}})',{'nested':{'i32':'double'}}),
]

HOST_CASES=[
    ('host-unchanged','',{'i32':'int','nested':{'i32':'int'},'arr':['int','double','long']},1),
    ('host-same-path-values',"d.i32=1;d.nested.i32=1;d.arr[0]=1;d.arr[1]=1;d.arr[2]=Long('1');",
        {'i32':'double','nested':{'i32':'double'},'arr':['double','double','long']},1),
    ('host-different-path-values','d.i32=2;d.nested.i32=2;d.arr[0]=2;d.arr[1]=2;d.arr[2]=2;',
        {'i32':'double','nested':{'i32':'double'},'arr':['double','double','double']},2),
    ('host-delete-readd-values',"delete d.i32;delete d.nested.i32;delete d.arr[0];delete d.arr[1];delete d.arr[2];d.i32=1;d.nested.i32=1;d.arr[0]=1;d.arr[1]=1;d.arr[2]=Long('1');",
        {'i32':'double','nested':{'i32':'double'},'arr':['double','double','long']},1),
    ('host-replace-same-components',"d.i32=1;d.nested={i32:1};d.arr=[1,1,Long('1')];",
        {'i32':'double','nested':{'i32':'double'},'arr':['double','double','long']},1),
    ('host-replace-different-components','d.i32=2;d.nested={i32:2};d.arr=[2,2,2];',
        {'i32':'double','nested':{'i32':'double'},'arr':['double','double','double']},2),
    ('host-delete-readd-components',"delete d.i32;delete d.nested;delete d.arr;d.i32=1;d.nested={i32:1};d.arr=[1,1,Long('1')];",
        {'i32':'double','nested':{'i32':'double'},'arr':['double','double','long']},1),
]


def read_copy_and_verify_seed(session,before,identity):
    raw=session.connection.get_database(session.config.database,codec_options=RAW_CODEC).docs
    assert raw.find_one({'_id':'seed'}).raw==before
    result=session.connection.get_database(session.config.database,codec_options=BSON_CODEC).docs.find_one({'_id':identity})
    assert result is not None and result['marker']=='synthetic-host-reference'
    return result


@pytest.mark.parametrize('identity,expression,expected',FRESH_CASES,ids=[v[0]for v in FRESH_CASES])
def test_actual_reference_fresh_copies_do_not_inherit_host_int32_metadata(engine,seeded,identity,expression,expected):
    session,before=seeded
    result=engine.execute(session,f"const d=db.docs.findOne({{_id:'seed'}});const copy={expression};copy._id='{identity}';copy.marker=d.marker;db.docs.insertOne(copy)")
    assert result.child_reaped and not result.error
    assert shape(read_copy_and_verify_seed(session,before,identity))==expected


@pytest.mark.parametrize('identity,assignments,expected,value',HOST_CASES,ids=[v[0]for v in HOST_CASES])
def test_actual_reference_host_path_assignment_invalidates_even_same_value(engine,seeded,identity,assignments,expected,value):
    session,before=seeded
    result=engine.execute(session,f"const d=db.docs.findOne({{_id:'seed'}});d._id='{identity}';{assignments}db.docs.insertOne(d)")
    assert result.child_reaped and not result.error
    copied=read_copy_and_verify_seed(session,before,identity)
    assert shape(copied)==expected
    assert copied['i32']==copied['nested']['i32']==copied['arr'][0]==value


def test_actual_reference_host_primitives_and_data_descriptors(engine,seeded):
    session,_=seeded
    result=engine.execute(session,"""
const d=db.docs.findOne({_id:'seed'});const p=Object.getOwnPropertyDescriptor(d,'i32');
({i32:typeof d.i32,nested:typeof d.nested.i32,arr:d.arr.map(v=>typeof v),equal:d.i32===1,
 nestedPlain:Object.getPrototypeOf(d.nested)===Object.prototype,arrayPlain:Object.getPrototypeOf(d.arr)===Array.prototype,
 hostPlain:Object.getPrototypeOf(d)===Object.prototype,ownNames:Object.getOwnPropertyNames(d),
 descriptor:{writable:p.writable,enumerable:p.enumerable,configurable:p.configurable,accessor:!!(p.get||p.set),valueType:typeof p.value},
 explicitType:typeof Int32(1),explicitEquals:Int32(1)===1,explicitString:String(Int32(1)),longString:String(Long('9007199254740995'))});
""")
    assert result.value=={'i32':'number','nested':'number','arr':['number','number','object'],'equal':True,
        'nestedPlain':True,'arrayPlain':True,'hostPlain':False,'ownNames':['_id','marker','i32','nested','arr'],
        'descriptor':{'writable':True,'enumerable':True,'configurable':True,'accessor':False,'valueType':'number'},
        'explicitType':'object','explicitEquals':False,'explicitString':'Int32("1")','longString':'Long("9007199254740995")'}


def test_actual_reference_nonfinite_and_negative_zero_wire_values(engine,session):
    engine.execute(session,"db.docs.insertOne({_id:'nonfinite',nan:NaN,positiveInfinity:Infinity,negativeInfinity:-Infinity,negativeZero:-0})")
    doc=session.connection[session.config.database].docs.find_one({'_id':'nonfinite'})
    assert all(type(doc[name])is float for name in ('nan','positiveInfinity','negativeInfinity','negativeZero'))
    assert math.isnan(doc['nan'])and doc['positiveInfinity']==math.inf and doc['negativeInfinity']==-math.inf
    assert struct.pack('<d',doc['negativeZero'])==b'\0\0\0\0\0\0\0\x80'


def test_host_metadata_changes_only_the_assigned_path_and_has_no_visible_marker(engine,seeded):
    """Additional preservation contract, beyond the reference's all-path cases."""
    session,before=seeded
    engine.execute(session,"const d=db.docs.findOne({_id:'seed'});d._id='isolated';d.i32=1;db.docs.insertOne(d)")
    doc=read_copy_and_verify_seed(session,before,'isolated')
    assert shape(doc)=={'i32':'double','nested':{'i32':'int'},'arr':['int','double','long']}
    assert set(doc)=={'_id','marker','i32','nested','arr'}


def test_host_metadata_does_not_execute_accessors_or_bypass_readonly(engine,seeded):
    session,before=seeded
    result=engine.execute(session,"""
const d=db.docs.findOne({_id:'seed'});d._id='must_not_write';let touched=0;
Object.defineProperty(d.nested,'i32',{get(){touched++;return 1},enumerable:true,configurable:true});
let rejected=false;try{db.docs.insertOne(d)}catch(e){rejected=true}({touched,rejected});
""")
    assert result.value=={'touched':0.0,'rejected':True}
    assert all(receipt.verb!='insert'for receipt in result.commands)
    assert session.connection[session.config.database].docs.count_documents({})==1
    session.config.read_only=True
    with pytest.raises(JavaScriptError):
        engine.execute(session,"const d=db.docs.findOne({_id:'seed'});d._id='blocked';db.docs.insertOne(d)")
    assert session.connection.get_database(session.config.database,codec_options=RAW_CODEC).docs.find_one({'_id':'seed'}).raw==before
