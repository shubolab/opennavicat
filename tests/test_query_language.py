"""Real old workspace/history migrations preserve the old numeric interpreter."""
from contextlib import closing
import json
import sqlite3

import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.query_language import LEGACY, MONGO_JS, query_language
from opennavicat.storage import Store


def test_missing_language_means_legacy_except_deliberately_new_mongo_query():
    assert query_language(None, driver='mongodb') == LEGACY
    assert query_language(None, driver='mongodb', new=True) == MONGO_JS
    assert query_language(None, driver='sqlite', new=True) == LEGACY
    with pytest.raises(ValueError): query_language('future-language', driver='mongodb')
    with pytest.raises(ValueError): query_language(MONGO_JS, driver='sqlite')


def test_real_old_history_drafts_and_favorites_migrate_without_changing_text_or_numbers(tmp_path):
    source = '{"insert":"docs","documents":[{"_id":1,"n":1}]}'
    (tmp_path/'workspace.json').write_text(json.dumps({'drafts':[{'sql':source,'connection_id':'mongo'}],
        'favorites':[{'name':'Saved','sql':source,'connection_id':'mongo'}]}))
    with closing(sqlite3.connect(tmp_path/'history.sqlite3')) as db, db:
        db.execute('CREATE TABLE history (id INTEGER PRIMARY KEY, created TEXT, connection_id TEXT, connection_name TEXT, sql TEXT, elapsed REAL, status TEXT)')
        db.execute('INSERT INTO history VALUES (1,"old","mongo","Synthetic",?,1.0,"success")',(source,))
    store = Store(tmp_path)
    assert store.history()[0]['query_language'] == LEGACY
    assert store.history()[0]['sql'] == source
    assert all(store.state[k][0]['query_language'] == LEGACY and store.state[k][0]['sql'] == source for k in ('drafts','favorites'))
    store.record(ConnectionConfig('mongo','Synthetic',driver='mongodb'), '1n', 1, 'success', query_language=MONGO_JS)
    store.add_favorite('JS','1n','mongo',query_language=MONGO_JS)
    again = Store(tmp_path)
    assert again.history()[0]['query_language'] == MONGO_JS
    assert again.state['favorites'][1]['query_language'] == MONGO_JS
    assert again.history()[1]['query_language'] == LEGACY  # Migration is idempotent.


def test_file_language_is_bound_to_exact_text_not_extension_or_path_alone(tmp_path):
    store = Store(tmp_path/'workspace')
    filename = tmp_path/'old.js'
    assert store.saved_file_language(filename,'db.docs.insertOne({_id:1,n:1})') is None
    store.remember_file_language(filename,'db.docs.insertOne({_id:1,n:1})',LEGACY)
    assert Store(store.directory).saved_file_language(filename,'db.docs.insertOne({_id:1,n:1})') == LEGACY
    assert store.saved_file_language(filename,'db.docs.insertOne({_id:1,n:2})') is None


def test_explicit_null_language_is_missing_metadata_not_a_request_for_new_js(tmp_path):
    (tmp_path/'workspace.json').write_text(json.dumps({'drafts':[{'sql':'1','query_language':None}],
        'favorites':[{'sql':'1','query_language':None}]}))
    with closing(sqlite3.connect(tmp_path/'history.sqlite3')) as db, db:
        db.execute('CREATE TABLE history (id INTEGER PRIMARY KEY,created TEXT,connection_id TEXT,connection_name TEXT,sql TEXT,elapsed REAL,status TEXT,query_language TEXT)')
        db.execute('INSERT INTO history(sql,query_language) VALUES ("1",NULL)')
    store=Store(tmp_path)
    assert store.history()[0]['query_language']==LEGACY
    assert all(store.state[key][0]['query_language']==LEGACY for key in ('drafts','favorites'))


@pytest.mark.parametrize('invalid',[{},['legacy']])
def test_malformed_language_metadata_is_a_value_error_and_never_changes_store(tmp_path,invalid):
    store=Store(tmp_path)
    config=ConnectionConfig('mongo','Synthetic',driver='mongodb')
    with pytest.raises(ValueError):query_language(invalid,driver='mongodb')
    with pytest.raises(ValueError):store.record(config,'1',0,'success',query_language=invalid)
    with pytest.raises(ValueError):store.add_favorite('Saved','1','mongo',query_language=invalid)
    with pytest.raises(ValueError):store.remember_file_language(tmp_path/'query.js','1',invalid)
    assert store.history()==[] and store.state['favorites']==[] and 'query_files' not in store.state


@pytest.mark.parametrize('invalid',[{},['legacy']])
def test_malformed_draft_language_cannot_crash_startup_or_lose_original_draft(qtbot,tmp_path,monkeypatch,invalid):
    from dataclasses import asdict
    from opennavicat.mongodb_session import MongoSession
    from opennavicat.ui.window import MainWindow
    errors=[]
    monkeypatch.setattr(MainWindow,'error',lambda self,error:errors.append(error))
    monkeypatch.setattr(MongoSession,'connect',lambda self:pytest.fail('Metadata migration must not connect a database'))
    config=ConnectionConfig('mongo','Synthetic',driver='mongodb',database='owned')
    draft={'connection_id':'mongo','sql':'db.docs.drop()','query_language':invalid,'modified':True}
    (tmp_path/'workspace.json').write_text(json.dumps({'connections':[asdict(config)],'drafts':[draft],'navicat_auto_discover':False}))
    window=MainWindow(Store(tmp_path))
    def close(widget):
        widget.close()
        assert widget._closed and not widget.tasks
    qtbot.addWidget(window,before_close_func=close)
    assert len(errors)==1 and isinstance(errors[0],ValueError)
    assert window._unrestored_query_drafts==[draft] and not window.sessions
    close(window)
    assert draft in Store(tmp_path).state['drafts']
