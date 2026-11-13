"""Exercise the real designer fixture under an allowed DeferredDelete schedule."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = "tests/test_ui.py::test_designer_review_execute_and_table_browser"
CHILD = r'''
import functools,json,pathlib,sys,threading,weakref
import pytest
output=pathlib.Path(sys.argv[1]); events=[]; windows=[]
def record(event, **fields):
 events.append(dict(event=event,thread=threading.get_ident(),**fields))
class Probe:
 def pytest_configure(self,config):
  from PySide6.QtCore import QCoreApplication,QEvent
  from shiboken6 import isValid
  from opennavicat.ui.window import MainWindow
  from opennavicat.database import DatabaseSession
  import pytestqt.plugin as plugin
  import pytestqt.qtbot as qtbot
  def state(window):
   pool=getattr(window,'pool',None)
   return dict(window_valid=isValid(window),pool_valid=pool is not None and isValid(pool))
  original=MainWindow.__init__
  @functools.wraps(original)
  def init(window,*args,**kwargs):
   original(window,*args,**kwargs); windows.append(weakref.ref(window))
   window.destroyed.connect(lambda *_:record('window.destroyed'))
   window.pool.destroyed.connect(lambda *_:record('pool.destroyed'))
   record('window.created',**state(window))
  MainWindow.__init__=init
  old_close=MainWindow.closeEvent
  @functools.wraps(old_close)
  def close(window,event):
   record('window.close.begin',**state(window))
   try:return old_close(window,event)
   finally:record('window.close.end',accepted=event.isAccepted())
  MainWindow.closeEvent=close
  old_session_close=DatabaseSession.close
  @functools.wraps(old_session_close)
  def close_session(session):
   had_connection=session.connection is not None
   result=old_session_close(session)
   record('session.closed',had_connection=had_connection,connection_is_none=session.connection is None)
   return result
  DatabaseSession.close=close_session
  old_fetch=DatabaseSession.fetch_table
  @functools.wraps(old_fetch)
  def fetch(session,*args,**kwargs):
   result=old_fetch(session,*args,**kwargs)
   record('session.fetched')
   return result
  DatabaseSession.fetch_table=fetch
  old_add=qtbot.QtBot.addWidget
  @functools.wraps(old_add)
  def add(bot,widget,*args,before_close_func=None,**kwargs):
   callback=before_close_func
   if isinstance(widget,MainWindow) and callback is not None:
    def observed(window):
     record('fixture.prepare.begin',**state(window))
     callback(window)
     record('fixture.prepare.end',active=window.pool.activeThreadCount(),tasks=len(window.tasks),**state(window))
    before_close_func=observed
   return old_add(bot,widget,*args,before_close_func=before_close_func,**kwargs)
  qtbot.QtBot.addWidget=add
  old_widgets=plugin._close_widgets
  def close_widgets(item):
   record('pytestqt.close_widgets.begin')
   result=old_widgets(item)
   record('pytestqt.close_delete_requested')
   # Deliver queued DeferredDelete events after the real close_widgets()
   # requests. The product and its predicates remain unchanged.
   QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
   record('pytestqt.deferred_delivered',states=[state(w()) for w in windows if w() is not None])
   return result
  plugin._close_widgets=close_widgets
 def pytest_runtest_logreport(self,report):
  record('report',when=report.when,outcome=report.outcome)
code=pytest.main(['-q','-W','error',sys.argv[2],'--basetemp='+sys.argv[3]],plugins=[Probe()])
output.write_text(json.dumps(dict(exit_code=int(code),events=events),indent=2)+'\n')
raise SystemExit(code)
'''


def test_designer_fixture_cleans_before_pytestqt_delivers_parent_delete(tmp_path):
    """Real serial SQLite work and real framework close, in a separate Qt app."""
    evidence = tmp_path / "lifecycle.json"
    env = {key: value for key, value in os.environ.items()
           if key != "PYTEST_ADDOPTS" and not key.startswith(
               ("OPENNAVICAT_TEST", "OPENNAVICAT_MONGODB", "OPENNAVICAT_ORACLE"))}
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(evidence), TARGET, str(tmp_path / "child")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert evidence.is_file(), result.stdout + result.stderr
    recorded = json.loads(evidence.read_text())
    assert result.returncode == recorded["exit_code"] == 0, result.stdout + result.stderr
    events = recorded["events"]
    names = [event["event"] for event in events]
    assert [(event["when"], event["outcome"]) for event in events
            if event["event"] == "report"] == [
                ("setup", "passed"), ("call", "passed"), ("teardown", "passed")]
    before = next(event for event in events if event["event"] == "fixture.prepare.begin")
    after = next(event for event in events if event["event"] == "fixture.prepare.end")
    assert before["window_valid"] and before["pool_valid"]
    assert after["window_valid"] and after["pool_valid"]
    assert after["active"] == after["tasks"] == 0
    assert names.index("fixture.prepare.end") < names.index("window.close.begin")
    close_events = [event for event in events if event["event"] == "window.close.end"]
    assert len(close_events) == 1 and close_events[0]["accepted"] is True
    assert names.index("window.close.end") < names.index("window.destroyed")
    assert names.index("window.destroyed") <= names.index("pool.destroyed")
    assert names.index("pool.destroyed") < names.index("pytestqt.deferred_delivered")
    closed = [(index, event) for index, event in enumerate(events)
              if event["event"] == "session.closed" and event["had_connection"]]
    assert closed and all(event["connection_is_none"] and
                          index < names.index("window.destroyed") for index, event in closed)
    gui_thread = before["thread"]
    assert any(event["event"] == "session.fetched" and event["thread"] != gui_thread
               for event in events)
    delivered = next(event for event in events if event["event"] == "pytestqt.deferred_delivered")
    assert delivered["states"] and all(not state["window_valid"] and not state["pool_valid"]
                                       for state in delivered["states"])
