"""공통 런타임 도구: 해시, 원자적 쓰기, 시각, 오프라인 가드, 메모리 측정."""
from __future__ import annotations

import hashlib
import importlib.metadata
import ipaddress
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


# ---------------------------------------------------------------- 시각·해시
def utc_now() -> str:
    """UTC ISO 8601 (밀리초). 스펙 9.2: 모든 시각은 UTC로 저장한다."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------- 원자적 쓰기
def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """임시 파일에 쓰고 fsync 후 교체한다. 중단되어도 불완전한 파일이 남지 않는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: str | Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str | Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------- 오프라인 가드
class NetworkBlockedError(RuntimeError):
    """오프라인 모드에서 루프백 이외의 네트워크 접근이 시도됨."""


_guard_state: dict[str, Any] = {"installed": False, "attempts": []}


def configure_offline_environment() -> None:
    """모델 로더가 원격을 찾지 않도록 환경 변수를 고정한다 (스펙 15.1).

    환경 변수만으로 오프라인이 보장되지는 않으므로 install_network_guard와 함께 쓴다.
    """
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""


def _is_loopback_host(host: Any) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def _is_loopback_address(address: Any) -> bool:
    # AF_UNIX 등 튜플이 아닌 주소는 로컬 통신이다.
    if not isinstance(address, tuple) or not address:
        return True
    return _is_loopback_host(address[0])


def install_network_guard() -> None:
    """루프백 외 소켓 연결·전송·DNS 조회를 차단한다.

    Python 소켓 계층의 가드이며 네이티브 라이브러리가 직접 여는 연결은 막지 못한다.
    운영 환경의 최종 보장은 OS/방화벽 수준 차단과 네트워크 차단 통합시험이다.
    """
    if _guard_state["installed"]:
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_getaddrinfo = socket.getaddrinfo
    original_lookups = {name: getattr(socket, name) for name in ("gethostbyname", "gethostbyname_ex", "gethostbyaddr")}

    def _block(kind: str, target: Any) -> None:
        _guard_state["attempts"].append({"kind": kind, "target": repr(target)})
        raise NetworkBlockedError(f"오프라인 모드: 네트워크 접근이 차단되었습니다 ({kind}: {target!r})")

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if not _is_loopback_address(address):
            _block("connect", address)
        return original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        if not _is_loopback_address(address):
            _block("connect", address)
        return original_connect_ex(self, address)

    def guarded_sendto(self: socket.socket, *args: Any) -> Any:
        if args and not _is_loopback_address(args[-1]):
            _block("sendto", args[-1])
        return original_sendto(self, *args)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_loopback_host(host):
            _block("dns", host)
        return original_getaddrinfo(host, *args, **kwargs)

    def guard_lookup(name: str) -> Any:
        original = original_lookups[name]

        def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
            if not _is_loopback_host(host):
                _block("dns", host)
            return original(host, *args, **kwargs)

        return guarded

    for lookup_name in original_lookups:
        setattr(socket, lookup_name, guard_lookup(lookup_name))
    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = guarded_sendto  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    _guard_state.update(
        installed=True,
        originals=(original_connect, original_connect_ex, original_sendto, original_getaddrinfo),
        lookups=original_lookups,
    )


def uninstall_network_guard() -> None:
    """시험용: 가드를 해제한다."""
    if not _guard_state["installed"]:
        return
    connect, connect_ex, sendto, getaddrinfo = _guard_state.pop("originals")
    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = sendto  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    for lookup_name, original in _guard_state.pop("lookups").items():
        setattr(socket, lookup_name, original)
    _guard_state["installed"] = False


def network_guard_installed() -> bool:
    return bool(_guard_state["installed"])


def network_attempts() -> list[dict[str, str]]:
    return list(_guard_state["attempts"])


def enter_offline_mode() -> None:
    configure_offline_environment()
    install_network_guard()


# ---------------------------------------------------------------- 환경 기록
def environment_manifest() -> dict[str, Any]:
    packages = sorted(
        {(dist.metadata["Name"] or "").lower(): dist.version for dist in importlib.metadata.distributions()}.items()
    )
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": {name: version for name, version in packages if name},
    }


def environment_manifest_hash(manifest: dict[str, Any] | None = None) -> str:
    return sha256_text(canonical_json(manifest or environment_manifest()))


def code_commit(repo_dir: str | Path | None = None) -> str | None:
    """git HEAD commit. 저장소가 아니거나 commit이 없으면 None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir) if repo_dir else None,
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) >= 40 else None


# ---------------------------------------------------------------- 메모리
class MemoryBudgetExceeded(RuntimeError):
    """프로세스 트리 RSS가 예산을 넘었고 더 줄일 방법이 없음."""


class MemoryMonitor:
    """프로세스 트리(자식 포함) RSS를 측정하고 peak을 추적한다 (스펙 14.2, 15.2)."""

    def __init__(self, budget_gib: float) -> None:
        import psutil

        self._psutil = psutil
        self._process = psutil.Process()
        self.budget_bytes = int(budget_gib * (1 << 30))
        self.peak_bytes = 0

    def rss_bytes(self) -> int:
        psutil = self._psutil
        total = self._process.memory_info().rss
        try:
            children = self._process.children(recursive=True)
        except psutil.Error:
            children = []
        for child in children:
            try:
                total += child.memory_info().rss
            except psutil.Error:
                continue
        self.peak_bytes = max(self.peak_bytes, total)
        return total

    def over_budget(self, fraction: float = 1.0) -> bool:
        return self.rss_bytes() > self.budget_bytes * fraction

    def os_peak_bytes(self) -> int:
        """OS가 보고하는 현재 프로세스의 peak RSS (자식 제외)."""
        info = self._process.memory_info()
        peak = getattr(info, "peak_wset", 0)  # Windows
        if not peak:
            try:
                import resource

                value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                peak = value if sys.platform == "darwin" else value * 1024
            except (ImportError, OSError):
                peak = 0
        return int(peak)

    def summary(self) -> dict[str, float]:
        self.rss_bytes()
        peak = max(self.peak_bytes, self.os_peak_bytes())
        return {
            "peak_rss_gib": round(peak / (1 << 30), 3),
            "budget_gib": round(self.budget_bytes / (1 << 30), 3),
            "within_budget": peak <= self.budget_bytes,
        }


def total_memory_gib() -> float:
    import psutil

    return round(psutil.virtual_memory().total / (1 << 30), 1)
