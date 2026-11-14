"""Own a Windows test process and its ordinary CreateProcess descendants.

Windows 10/Server 2016+ only. JOB_LIST binds the new suspended process during
creation; membership is verified before its initial thread is resumed. The
unnamed job handle is not inherited, and neither breakaway limit is enabled.
WMI/remote process launch is outside this ordinary subprocess ownership scope.

API contracts:
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
https://learn.microsoft.com/windows/win32/procthread/job-objects
https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812
"""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path
import subprocess
import time


DWORD, WORD, BOOL = C.c_uint32, C.c_uint16, C.c_int32
HANDLE, SIZE_T, PVOID = C.c_void_p, C.c_size_t, C.c_void_p
WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 258
CREATE_SUSPENDED, CREATE_NEW_PROCESS_GROUP = 0x4, 0x200
CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT = 0x400, 0x80000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x20002, 0x2000D


class BasicLimit(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_int64), ("PerJobUserTimeLimit", C.c_int64),
        ("LimitFlags", DWORD), ("MinimumWorkingSetSize", SIZE_T), ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", DWORD), ("Affinity", SIZE_T), ("PriorityClass", DWORD), ("SchedulingClass", DWORD)]


class IoCounters(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimit(C.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]


class Accounting(C.Structure):
    _fields_ = [(name, C.c_int64) for name in ("TotalUserTime", "TotalKernelTime",
        "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
        (name, DWORD) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]


class StartupInfo(C.Structure):
    _fields_ = [("cb", DWORD), ("lpReserved", C.c_wchar_p), ("lpDesktop", C.c_wchar_p),
        ("lpTitle", C.c_wchar_p)] + [(name, DWORD) for name in ("dwX", "dwY", "dwXSize", "dwYSize",
        "dwXCountChars", "dwYCountChars", "dwFillAttribute", "dwFlags")] + [
        ("wShowWindow", WORD), ("cbReserved2", WORD), ("lpReserved2", PVOID),
        ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE)]


class StartupInfoEx(C.Structure):
    _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", PVOID)]


class ProcessInfo(C.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE), ("dwProcessId", DWORD), ("dwThreadId", DWORD)]


class WindowsAPI:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows child ownership requires native Windows")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([PVOID, C.c_wchar_p], HANDLE),
            "SetInformationJobObject": ([HANDLE, C.c_int32, PVOID, DWORD], BOOL),
            "QueryInformationJobObject": ([HANDLE, C.c_int32, PVOID, DWORD, C.POINTER(DWORD)], BOOL),
            "InitializeProcThreadAttributeList": ([PVOID, DWORD, DWORD, C.POINTER(SIZE_T)], BOOL),
            "UpdateProcThreadAttribute": ([PVOID, DWORD, SIZE_T, PVOID, SIZE_T, PVOID, C.POINTER(SIZE_T)], BOOL),
            "DeleteProcThreadAttributeList": ([PVOID], None),
            "CreateProcessW": ([C.c_wchar_p, C.c_wchar_p, PVOID, PVOID, BOOL, DWORD, PVOID,
                C.c_wchar_p, C.POINTER(StartupInfo), C.POINTER(ProcessInfo)], BOOL),
            "IsProcessInJob": ([HANDLE, HANDLE, C.POINTER(BOOL)], BOOL),
            "ResumeThread": ([HANDLE], DWORD),
            "WaitForSingleObject": ([HANDLE, DWORD], DWORD),
            "GetExitCodeProcess": ([HANDLE, C.POINTER(DWORD)], BOOL),
            "TerminateJobObject": ([HANDLE, DWORD], BOOL),
            "TerminateProcess": ([HANDLE, DWORD], BOOL),
            "CloseHandle": ([HANDLE], BOOL),
            "GenerateConsoleCtrlEvent": ([DWORD, DWORD], BOOL),
            "GetHandleInformation": ([HANDLE, C.POINTER(DWORD)], BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result

    def check(self, value):
        if not value:
            raise C.WinError(C.get_last_error())
        return value

    def job(self):
        job = self.check(self.k.CreateJobObjectW(None, None))
        try:
            limits = ExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self.check(self.k.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)))
            flags = DWORD()
            self.check(self.k.GetHandleInformation(job, C.byref(flags)))
            if flags.value & 1:
                raise RuntimeError("Owned job must not be inherited")
            return job
        except BaseException:
            self.k.CloseHandle(job)
            raise

    def create(self, argv, env, cwd, job, stdout, stdin):
        # Attribute values and their backing buffer live until Delete*. The
        # job enters via JOB_LIST, never a post-spawn PID lookup/assignment.
        size = SIZE_T()
        self.k.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
        if C.get_last_error() != 122 or not size.value:
            raise C.WinError(C.get_last_error())
        attributes = C.create_string_buffer(size.value)
        initialized = False
        info = ProcessInfo()
        try:
            self.check(self.k.InitializeProcThreadAttributeList(attributes, 2, 0, C.byref(size)))
            initialized = True
            handles = (HANDLE * 2)(stdout, stdin)
            jobs = (HANDLE * 1)(job)
            for key, value in ((PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles), (PROC_THREAD_ATTRIBUTE_JOB_LIST, jobs)):
                self.check(self.k.UpdateProcThreadAttribute(attributes, 0, key, value, C.sizeof(value), None, None))
            startup = StartupInfoEx()
            startup.StartupInfo.cb = C.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = stdin
            startup.StartupInfo.hStdOutput = startup.StartupInfo.hStdError = stdout
            startup.lpAttributeList = C.cast(attributes, PVOID)
            command = C.create_unicode_buffer(subprocess.list2cmdline(argv))
            block = C.create_unicode_buffer("\0".join(key + "=" + value for key, value in
                sorted(env.items(), key=lambda pair: pair[0].upper())) + "\0\0")
            flags = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT
            self.check(self.k.CreateProcessW(argv[0], command, None, None, True, flags, block,
                str(cwd), C.byref(startup.StartupInfo), C.byref(info)))
            return info
        except BaseException:
            # A Python signal can arrive after CreateProcess populated the
            # structure but before this method returns it to its caller.
            if info.hProcess:
                self.k.TerminateProcess(info.hProcess, 1)
                self.k.WaitForSingleObject(info.hProcess, 5000)
                self.k.CloseHandle(info.hProcess)
            if info.hThread:
                self.k.CloseHandle(info.hThread)
            raise
        finally:
            if initialized:
                self.k.DeleteProcThreadAttributeList(attributes)

    def bound(self, process, job):
        result = BOOL()
        self.check(self.k.IsProcessInJob(process, job, C.byref(result)))
        return bool(result.value)

    def resume(self, thread):
        count = self.k.ResumeThread(thread)
        if count != 1:
            raise RuntimeError("Owned initial thread did not have exactly one suspension")

    def exited(self, process, milliseconds=0):
        status = self.k.WaitForSingleObject(process, milliseconds)
        if status not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
            raise C.WinError(C.get_last_error())
        return status == WAIT_OBJECT_0

    def counts(self, job):
        accounting = Accounting()
        self.check(self.k.QueryInformationJobObject(job, 1, C.byref(accounting), C.sizeof(accounting), None))
        return {"active": accounting.ActiveProcesses, "total": accounting.TotalProcesses}

    def active(self, job):
        return self.counts(job)["active"]


def run_owned_child(argv, env, raw_log, *, cwd, timeout, grace=5, limit=4 * 1024 * 1024, _api=None):
    """Return a finite cleanup receipt and at most limit bytes of output.

    The on-disk log limit is polled and may be exceeded between polls. On any
    ordinary completion, surviving descendants are terminated before returning.
    KeyboardInterrupt (including a caller's SIGTERM handler) returns a failed
    interrupted receipt after cleanup. Uncatchable owner termination instead
    relies on the sole noninherited job handle's KILL_ON_JOB_CLOSE contract.
    """
    if os.name != "nt":
        raise RuntimeError("Windows child ownership requires native Windows")
    if not argv or not Path(argv[0]).is_absolute() or timeout <= 0 or grace < 0 or limit <= 0:
        raise ValueError("Owned child requires an absolute executable and positive bounds")
    import msvcrt
    api = _api or WindowsAPI()
    started = time.monotonic()
    job, process, thread = None, None, None
    result = {"returncode": None, "pid": None, "reaped": False, "timed_out": False,
        "output_limited": False, "interrupted": False, "job_bound_before_resume": False,
        "cleanup_scope": "owned-windows-job", "stdout_transport": "file-backed",
        "job_kill_on_close": False, "breakaway_allowed": None, "job_handle_inherited": None,
        "job_active_processes_zero": False, "job_handle_closed": False,
        "thread_handle_closed": False, "process_handle_closed": False}
    with Path(raw_log).open("x+b") as log, open(os.devnull, "rb") as stdin:
        try:
            job = api.job()
            result.update(job_kill_on_close=True, breakaway_allowed=False, job_handle_inherited=False)
            output_handle, input_handle = msvcrt.get_osfhandle(log.fileno()), msvcrt.get_osfhandle(stdin.fileno())
            try:
                os.set_handle_inheritable(output_handle, True)
                os.set_handle_inheritable(input_handle, True)
                info = api.create([str(arg) for arg in argv], env, cwd, job, output_handle, input_handle)
                process, thread = info.hProcess, info.hThread
                result["pid"] = info.dwProcessId
            finally:
                os.set_handle_inheritable(output_handle, False)
                os.set_handle_inheritable(input_handle, False)
            if not api.bound(process, job):
                raise RuntimeError("Owned suspended process is not in its creation job")
            result["job_bound_before_resume"] = True
            api.resume(thread)
            deadline = time.monotonic() + timeout
            while not api.exited(process, 20):
                result["output_limited"] = os.fstat(log.fileno()).st_size > limit
                result["timed_out"] = time.monotonic() >= deadline
                if result["output_limited"] or result["timed_out"]:
                    break
        except KeyboardInterrupt:
            result["interrupted"] = True
        except Exception as error:
            result["error_class"] = type(error).__name__
            if isinstance(getattr(error, "winerror", None), int):
                result["winerror"] = error.winerror
        finally:
            try:
                if process:
                    if not api.exited(process) and result["job_bound_before_resume"]:
                        api.k.GenerateConsoleCtrlEvent(1, result["pid"])  # CTRL_BREAK_EVENT
                        api.exited(process, round(grace * 1000))
                if job and api.active(job):
                    api.check(api.k.TerminateJobObject(job, 1))
                if process:
                    if not result["job_bound_before_resume"] and not api.exited(process):
                        # A membership verification error must never resume a
                        # suspended leader. Terminate via our creation HANDLE.
                        api.check(api.k.TerminateProcess(process, 1))
                    result["reaped"] = api.exited(process, 5000)
                    if result["reaped"]:
                        code = DWORD()
                        api.check(api.k.GetExitCodeProcess(process, C.byref(code)))
                        result["returncode"] = code.value
                if job:
                    deadline = time.monotonic() + 5
                    while True:
                        counts = api.counts(job)
                        result["job_total_processes"] = counts["total"]
                        if counts["active"] == 0:
                            result["job_active_processes_zero"] = True
                            break
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(.02)
            except Exception as error:
                result["cleanup_error_class"] = type(error).__name__
            finally:
                for handle, key in ((thread, "thread_handle_closed"), (process, "process_handle_closed"), (job, "job_handle_closed")):
                    if handle:
                        result[key] = bool(api.k.CloseHandle(handle))
                    else:
                        result[key] = True
            size = os.fstat(log.fileno()).st_size
            log.seek(0)
            result.update(observed_log_bytes=size, output_limited=result["output_limited"] or size > limit,
                retained_output_bytes_limit=limit, output=log.read(limit).decode("utf-8", errors="replace"))
    result.update(log_handle_closed=log.closed, stdin_handle_closed=stdin.closed,
        descendant_cleanup_claimed=result["job_active_processes_zero"], seconds=round(time.monotonic() - started, 3))
    return result


def process_passed(result):
    return (result.get("returncode") == 0 and not any(result.get(key) for key in
            ("timed_out", "output_limited", "interrupted", "error_class", "cleanup_error_class"))
        and all(result.get(key) is True for key in ("reaped", "job_bound_before_resume",
            "job_active_processes_zero", "job_handle_closed", "thread_handle_closed", "process_handle_closed",
            "log_handle_closed", "stdin_handle_closed")))
