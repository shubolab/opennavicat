"""Actual main-window JS routing, historical semantics and job-owned Stop."""
from dataclasses import replace
import json
import threading

import pytest
from bson import BSON, Int64
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog

from opennavicat.js_engine.runtime import JavaScriptResult
from opennavicat.query_language import LEGACY, MONGO_JS
from opennavicat.storage import Store
from opennavicat.ui.mongo_native_query import NativeMongoQueryPage
from opennavicat.ui.query import QueryPage
from opennavicat.ui.window import MainWindow
from test_mongo_window import mongo_window, mongodb_service, idle


def test_real_mainwindow_new_js_runs_loop_typed_writes_and_cancel_without_changing_legacy_semantics(qtbot,mongo_window):
    window,config,database,schema,errors,threads=mongo_window
    page=window.new_query(connection_id=config.id)
    assert isinstance(page,NativeMongoQueryPage)
    page.editor.setPlainText("for(let i=0;i<2;i++)db.docs.insertOne({_id:'js'+i,n:1,long:Long('9007199254740995')});db.docs.find({_id:{$in:['js0','js1']}}).sort({_id:1})")
    window.run_current();qtbot.waitUntil(lambda:not page.running,timeout=15000);idle(qtbot,window)
    assert not page.last_execution.error and len(page.current_result().rows)==2
    assert window.store.history()[0]['query_language']==MONGO_JS
    assert type(database.docs.find_one({'_id':'js0'})['n']) is float
    assert type(database.docs.find_one({'_id':'js0'})['long']) is Int64
    legacy=window.new_query(sql="db.runCommand({insert:'docs',documents:[{_id:'old-int',n:1}]})",connection_id=config.id,query_language=LEGACY)
    assert type(legacy) is QueryPage
    window.run_current();qtbot.waitUntil(lambda:not legacy.running,timeout=15000);idle(qtbot,window)
    assert type(database.docs.find_one({'_id':'old-int'})['n']) is int
    assert window.store.history()[0]['query_language']==LEGACY
    window.tabs.setCurrentWidget(page)
    page.editor.setPlainText('while(true){}');window.run_current();qtbot.wait(60);window.stop()
    qtbot.waitUntil(lambda:not page.running,timeout=10000);idle(qtbot,window)
    assert page.last_execution.cancelled and page.last_execution.child_reaped
    assert not errors


def test_real_mainwindow_unknown_file_language_cancel_does_not_open_or_execute_and_choice_is_hash_bound(qtbot,mongo_window,tmp_path,monkeypatch):
    window,config,database,schema,errors,threads=mongo_window
    filename=tmp_path/'older.js';filename.write_text("db.runCommand({insert:'docs',documents:[{_id:'from-file',n:1}]})")
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *_: (str(filename),''))
    count=window.tabs.count()
    monkeypatch.setattr(QInputDialog,'getItem',lambda *_: ('',False))
    window.open_sql()
    assert window.tabs.count()==count and database.docs.find_one({'_id':'from-file'}) is None
    monkeypatch.setattr(QInputDialog,'getItem',lambda *_: ('旧版 MongoDB 命令 / Canonical EJSON（保留原整数语义）',True))
    window.open_sql();page=window.tabs.currentWidget()
    assert type(page)is QueryPage and page.query_language==LEGACY
    window.run_current();qtbot.waitUntil(lambda:not page.running);idle(qtbot,window)
    assert type(database.docs.find_one({'_id':'from-file'})['n']) is int
    monkeypatch.setattr(QInputDialog,'getItem',lambda *_: pytest.fail('Unchanged file must keep its saved language'))
    window.open_sql();assert type(window.tabs.currentWidget())is QueryPage
    filename.write_text('1n')
    monkeypatch.setattr(QInputDialog,'getItem',lambda *_: ('MongoDB JavaScript（普通 Number 保存为 Double）',True))
    window.open_sql();assert isinstance(window.tabs.currentWidget(),NativeMongoQueryPage)


def test_real_mainwindow_restores_legacy_and_native_drafts_and_favorites_language(qtbot,mongo_window):
    window,config,database,schema,errors,threads=mongo_window
    window.store.state['drafts']=[{'connection_id':config.id,'sql':'({x:1})','schema':config.database},
        {'connection_id':config.id,'sql':'1n','schema':config.database,'query_language':MONGO_JS}]
    window.restore_drafts()
    assert type(window.tabs.widget(window.tabs.count()-2)) is QueryPage
    page=window.tabs.currentWidget();assert isinstance(page,NativeMongoQueryPage)
    window.store.add_favorite('Native','1n',config.id,query_language=page.query_language)
    reloaded=Store(window.store.directory)
    assert reloaded.state['favorites'][-1]['query_language']==MONGO_JS
    assert not errors


def test_legacy_stop_captures_old_marker_before_cancel_pool_handoff(qtbot,mongo_window,monkeypatch):
    window,config,database,schema,errors,threads=mongo_window
    session=window.sessions[config.id]
    page=window.new_query(connection_id=config.id,query_language=LEGACY)
    blocker_started,release_blocker=threading.Event(),threading.Event()
    a_started,release_a,b_started,release_b=[threading.Event() for _ in range(4)]
    cancelled=[]
    original=session.cancel
    def traced(*args,**kwargs):
        cancelled.append(kwargs.get('expected_comment'))
        return original(*args,**kwargs)
    monkeypatch.setattr(session,'cancel',traced)
    def blocker():
        blocker_started.set();assert release_blocker.wait(5)
    def op(started,release):
        with session._operation():
            started.set();assert release.wait(5)
    try:
        window.submit(blocker,pool=window.cancel_pool)
        qtbot.waitUntil(blocker_started.is_set)
        window.submit(lambda:op(a_started,release_a))
        qtbot.waitUntil(a_started.is_set)
        old=session.cancellation_marker();page.running=True
        window.stop();page.running=False
        release_a.set()
        window.submit(lambda:op(b_started,release_b))
        qtbot.waitUntil(b_started.is_set)
        current=session.cancellation_marker()
        assert current!=old
        release_blocker.set()
        qtbot.waitUntil(lambda:bool(cancelled))
        assert cancelled==[old]
        assert not session._cancel_requested.is_set() and session.cancellation_marker()==current
    finally:
        release_blocker.set();release_a.set();release_b.set()
        idle(qtbot,window)
    assert not errors


def test_unknown_draft_language_cannot_execute_or_disappear_on_workspace_close(qtbot,mongo_window):
    window,config,database,schema,errors,threads=mongo_window
    draft={'connection_id':config.id,'sql':'db.docs.drop()','query_language':'future-not-supported','modified':True}
    window.store.state['drafts']=[draft]
    count=window.tabs.count();window.restore_drafts()
    assert window.tabs.count()==count and len(errors)==1
    assert 'language' in errors[0]
    errors.clear()
    window.close();qtbot.waitUntil(lambda:window._closed,timeout=15000);idle(qtbot,window)
    assert draft in Store(window.store.directory).state['drafts']
    assert database.docs.count_documents({})==6


def test_object_browser_favorite_route_preserves_old_and_native_language(qtbot,mongo_window):
    window,config,database,schema,errors,threads=mongo_window
    window.store.state['favorites']=[
        {'connection_id':config.id,'name':'Older favorite','sql':"db.runCommand({insert:'docs',documents:[{_id:'favorite-old',n:1}]})"},
        {'connection_id':config.id,'name':'Native favorite','sql':"db.docs.insertOne({_id:'favorite-js',n:1})",'query_language':MONGO_JS}]
    window.show_objects('query');idle(qtbot,window)
    browser=window.object_page
    for label,expected in [('Older favorite',QueryPage),('Native favorite',NativeMongoQueryPage)]:
        row=next(i for i in range(browser.table.rowCount()) if browser.table.item(i,0).text()==label)
        browser.table.selectRow(row);browser.open_selected()
        page=window.tabs.currentWidget();assert type(page)is expected
        window.run_current();qtbot.waitUntil(lambda:not page.running,timeout=15000);idle(qtbot,window)
    assert type(database.docs.find_one({'_id':'favorite-old'})['n'])is int
    assert type(database.docs.find_one({'_id':'favorite-js'})['n'])is float
    assert not errors
