"""Real Windows owned-job lifetimes; non-Windows is explicitly unqualified."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_child_owner", ROOT / "scripts/windows_child_owner.py")
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
windows = pytest.mark.skipif(os.name != "nt", reason="Requires actual native Windows Job Objects")


def run(tmp_path, code, **kwargs):
    return owner.run_owned_child([sys.executable, "-u", "-c", code], dict(os.environ),
        tmp_path / "child.log", cwd=tmp_path, timeout=20, grace=.1, **kwargs)


def assert_cleanup(result):
    assert result["cleanup_scope"] == "owned-windows-job"
    assert result["job_bound_before_resume"] is True
    assert result["job_kill_on_close"] is True and result["breakaway_allowed"] is False
    assert result["job_handle_inherited"] is False
    for key in ("reaped", "job_active_processes_zero", "job_handle_closed", "thread_handle_closed",
                "process_handle_closed", "log_handle_closed", "stdin_handle_closed"):
        assert result[key] is True, (key, result)
    assert "cleanup_error_class" not in result


def test_non_windows_cannot_claim_job_qualification(tmp_path):
    if os.name == "nt":
        pytest.skip("Tests the unsupported-host contract")
    with pytest.raises(RuntimeError, match="native Windows"):
        run(tmp_path, "pass")
    assert not (tmp_path / "child.log").exists()


@windows
@pytest.mark.parametrize("exitcode", [0, 7])
def test_normal_and_nonzero_exit_are_reaped_with_real_job_accounting(tmp_path, exitcode):
    result = run(tmp_path, "import sys;print('generated owned stdout');sys.exit(" + str(exitcode) + ")")
    assert_cleanup(result)
    assert result["returncode"] == exitcode and "generated owned stdout" in result["output"]
    assert owner.process_passed(result) is (exitcode == 0)
    assert result["job_total_processes"] == 1


@windows
def test_unicode_environment_cwd_and_quoted_arguments_survive_native_creation(tmp_path):
    directory = tmp_path / "generated space 样例"
    directory.mkdir()
    values = ["space value", 'literal " quote', "中文"]
    env = dict(os.environ, OPENNAVICAT_GENERATED_ARGUMENT="generated 环境")
    code = "import os,sys,json;print(json.dumps([sys.argv[1:],os.getcwd(),os.environ['OPENNAVICAT_GENERATED_ARGUMENT']]))"
    result = owner.run_owned_child([sys.executable, "-u", "-c", code, *values], env,
        directory / "child.log", cwd=directory, timeout=20)
    assert_cleanup(result)
    assert owner.process_passed(result)
    assert json.loads(result["output"]) == [values, str(directory), "generated 环境"]


@windows
@pytest.mark.parametrize("mode", ["timeout", "output-limit"])
def test_live_child_timeout_and_output_limit_drain_job(tmp_path, mode):
    code = "import os,time;os.write(1,b'x'*20000);time.sleep(30)" if mode == "output-limit" else "import time;time.sleep(30)"
    result = owner.run_owned_child([sys.executable, "-u", "-c", code], dict(os.environ),
        tmp_path / "child.log", cwd=tmp_path, timeout=3 if mode == "timeout" else 20, grace=.1, limit=128)
    assert_cleanup(result)
    assert not owner.process_passed(result)
    assert result["timed_out"] is (mode == "timeout")
    assert result["output_limited"] is (mode == "output-limit")
    assert len(result["output"].encode()) <= 128


def tree_code(tmp_path, closed):
    # The actual grandchild self-expires as a test fallback and writes a marker
    # before its ancestors exit; its inherited pipe state cannot hide it.
    ready = tmp_path / "grandchild-ready.json"
    grandchild = ("import os,time,json;from pathlib import Path;"
        + ("os.close(1);os.close(2);" if closed else "")
        + "p=Path(" + repr(str(ready)) + ");temporary=p.with_suffix('.next');"
          "temporary.write_text(json.dumps({'pid':os.getpid()}));temporary.replace(p);time.sleep(30)")
    middle = ("import subprocess,sys,time;from pathlib import Path;"
        "subprocess.Popen([sys.executable,'-u','-c'," + repr(grandchild) + "]);"
        "p=Path(" + repr(str(ready)) + ");deadline=time.monotonic()+20\n"
        "while not p.exists() and time.monotonic()<deadline:time.sleep(.02)\n"
        "assert p.exists()")
    leader = ("import subprocess,sys;child=subprocess.Popen([sys.executable,'-u','-c'," + repr(middle)
        + "]);assert child.wait(timeout=25)==0;print('owned tree ready')")
    return leader, ready


@windows
@pytest.mark.parametrize("closed", [False, True], ids=["inherited-stdout", "closed-stdout"])
def test_leader_exit_still_cleans_real_child_and_grandchild(tmp_path, closed):
    code, ready = tree_code(tmp_path, closed)
    result = run(tmp_path, code)
    assert_cleanup(result)
    assert result["returncode"] == 0 and ready.is_file()
    assert result["job_total_processes"] >= 3
    assert owner.process_passed(result)


@windows
@pytest.mark.parametrize("failure", ["membership", "resume"])
def test_failure_before_resume_never_executes_child_code(tmp_path, failure):
    marker = tmp_path / "must-not-execute"
    api = owner.WindowsAPI()
    if failure == "membership":
        api.bound = lambda *_: False
    else:
        def refuse(*_args):
            raise RuntimeError("generated resume failure")
        api.resume = refuse
    result = run(tmp_path, "from pathlib import Path;Path(" + repr(str(marker)) + ").touch()", _api=api)
    assert not marker.exists() and not owner.process_passed(result)
    assert result["error_class"] == "RuntimeError" and result["reaped"]
    assert result["job_active_processes_zero"] and result["job_handle_closed"]
    assert result["thread_handle_closed"] and result["process_handle_closed"]


@windows
def test_create_failure_closes_empty_job_and_log_without_claiming_execution(tmp_path):
    result = owner.run_owned_child([str(tmp_path / "generated-absent.exe")], dict(os.environ),
        tmp_path / "child.log", cwd=tmp_path, timeout=10)
    assert result["pid"] is None and not result["job_bound_before_resume"]
    assert result["job_total_processes"] == 0 and result["job_active_processes_zero"]
    assert result["job_handle_closed"] and result["log_handle_closed"] and result["stdin_handle_closed"]
    assert not owner.process_passed(result) and result["error_class"] == "FileNotFoundError"


@windows
def test_external_supervisor_sigterm_closes_sole_job_handle_and_kills_descendant(tmp_path):
    code, ready = tree_code(tmp_path, True)
    # Keep the supervised leader alive so SIGTERM reaches the owner while its
    # job is active. The grandchild still closes stdout and retains no job handle.
    code += ";import time;time.sleep(30)"
    wrapper = ("import importlib.util,os,sys;from pathlib import Path;"
        "s=importlib.util.spec_from_file_location('job_owner'," + repr(str(ROOT / "scripts/windows_child_owner.py")) + ");"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "m.run_owned_child([sys.executable,'-u','-c'," + repr(code) + "],dict(os.environ),Path("
        + repr(str(tmp_path / "wrapper.log")) + "),cwd=" + repr(str(tmp_path)) + ",timeout=25,grace=.1)")
    api = owner.WindowsAPI()
    api.k.OpenProcess.argtypes = [owner.DWORD, owner.BOOL, owner.DWORD]
    api.k.OpenProcess.restype = owner.HANDLE
    descendant = None
    with subprocess.Popen([sys.executable, "-u", "-c", wrapper], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as supervisor:
        try:
            deadline = time.monotonic() + 20
            while not ready.exists() and supervisor.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert ready.exists(), supervisor.communicate(timeout=2)[1]
            # Open before signalling and retain this identity through the wait;
            # no destructive operation ever uses a PID lookup or PID tree.
            descendant = api.check(api.k.OpenProcess(0x100000, False, json.loads(ready.read_text())["pid"]))
            supervisor.send_signal(signal.SIGTERM)
            supervisor.wait(timeout=10)
            assert api.exited(descendant, 10000), "Job descendant survived supervisor SIGTERM"
        finally:
            if supervisor.poll() is None:
                supervisor.terminate(); supervisor.wait(timeout=10)
            if descendant:
                api.check(api.k.CloseHandle(descendant))
