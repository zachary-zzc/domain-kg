"""Linux native readers inherit filesystem isolation and cannot open network sockets."""

import ctypes
import errno
import os
import socket
import sys
from pathlib import Path

from domain_kg.errors import DomainError, ErrorCode


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class ArgumentRule(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint32),
        ("op", ctypes.c_uint32),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def confine(directory: Path) -> None:
    """Fail closed if the host cannot isolate native decoders/converters."""
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise OSError("no_new_privs")
        abi = int(libc.syscall(444, 0, 0, 1))
        if abi < 1:
            raise OSError("Landlock unavailable")
        handled = (1 << 13) - 1
        if abi >= 2:
            handled |= 1 << 13
        if abi >= 3:
            handled |= 1 << 14
        ruleset = Ruleset(handled)
        descriptor = int(libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0))
        if descriptor < 0:
            raise OSError("Landlock ruleset")
        try:
            roots = {
                Path("/usr"),
                Path("/lib"),
                Path("/lib64"),
                Path("/bin"),
                Path("/sbin"),
                Path(sys.prefix),
                Path(sys.base_prefix),
                Path(__file__).resolve().parents[2],
                Path("/etc/fonts"),
                Path("/etc/libreoffice"),
                Path("/etc/nsswitch.conf"),
                Path("/etc/passwd"),
                Path("/etc/ld.so.cache"),
                Path("/etc/localtime"),
                Path("/var/cache/fontconfig"),
                Path("/dev"),
                Path("/proc/self"),
                Path("/proc/meminfo"),
                Path("/proc/cpuinfo"),
                Path("/sys/devices/system/cpu"),
            }
            for root in (*roots, directory):
                if not root.exists():
                    continue
                access = handled if root == directory else (1 | 4 | (8 if root.is_dir() else 0))
                parent = os.open(root, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = PathRule(access, parent)
                    if libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) != 0:
                        raise OSError("Landlock path rule")
                finally:
                    os.close(parent)
            parent = os.open("/dev/null", os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(2 | 4, parent)
                if libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) != 0:
                    raise OSError("Landlock null device")
            finally:
                os.close(parent)
            if libc.syscall(446, descriptor, 0) != 0:
                raise OSError("Landlock restriction")
        finally:
            os.close(descriptor)
        seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
        seccomp.seccomp_init.restype = ctypes.c_void_p
        seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
        seccomp.seccomp_rule_add.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
        seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
        context = seccomp.seccomp_init(0x7FFF0000)
        if not context:
            raise OSError("seccomp initialization")
        try:
            for name in (
                b"connect",
                b"ptrace",
                b"process_vm_readv",
                b"process_vm_writev",
                b"pidfd_getfd",
            ):
                call = seccomp.seccomp_syscall_resolve_name(name)
                if (
                    call >= 0
                    and seccomp.seccomp_rule_add(context, 0x50000 | errno.EPERM, call, 0) != 0
                ):
                    raise OSError("seccomp rule")
            # LibreOffice needs a private local listening socket. It cannot
            # connect anywhere; INET/INET6/raw socket creation is also denied.
            seccomp.seccomp_rule_add_array.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_int,
                ctypes.c_uint,
                ctypes.POINTER(ArgumentRule),
            ]
            comparison = ArgumentRule(0, 1, socket.AF_UNIX, 0)  # SCMP_CMP_NE
            call = seccomp.seccomp_syscall_resolve_name(b"socket")
            if (
                call < 0
                or seccomp.seccomp_rule_add_array(
                    context, 0x50000 | errno.EPERM, call, 1, ctypes.byref(comparison)
                )
                != 0
            ):
                raise OSError("seccomp socket domain")
            if seccomp.seccomp_load(context) != 0:
                raise OSError("seccomp restriction")
        finally:
            seccomp.seccomp_release(context)
    except OSError as exc:
        raise DomainError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Native parsing requires Linux Landlock and libseccomp isolation.",
        ) from exc
