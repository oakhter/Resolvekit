from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class OSInfo:
    os_family: str
    label: str
    distro: str = ""
    arch: str = ""
    package_manager: str = ""
    is_wsl: bool = False
    docker_install_hint: str = ""
    python_install_hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _linux_distro() -> str:
    release = Path("/etc/os-release")
    if not release.exists():
        return "linux"
    data: dict[str, str] = {}
    for line in release.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value.strip().strip('"')
    return (data.get("ID") or data.get("NAME") or "linux").lower()


def _is_wsl() -> bool:
    if "WSL_DISTRO_NAME" in os.environ:
        return True
    try:
        text = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
        return "microsoft" in text or "wsl" in text
    except OSError:
        return False


def detect_os() -> OSInfo:
    system = platform.system().lower()
    arch = platform.machine().lower()
    if system == "darwin":
        silicon = arch in {"arm64", "aarch64"}
        return OSInfo(
            os_family="macos",
            label="macOS Apple Silicon" if silicon else "macOS Intel",
            arch=arch,
            package_manager="brew" if shutil.which("brew") else "",
            docker_install_hint="Install Docker Desktop: https://www.docker.com/products/docker-desktop/",
            python_install_hint="Install Python with Homebrew: brew install python",
        )
    if system == "linux":
        distro = _linux_distro()
        if distro in {"ubuntu", "debian", "linuxmint", "pop"}:
            pm = "apt"
            docker_hint = "Install Docker Engine/Compose with apt, or Docker Desktop with WSL integration."
            python_hint = "Install Python tooling: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
        elif distro in {"fedora", "rhel", "centos", "rocky", "almalinux"}:
            pm = "dnf"
            docker_hint = "Install Docker with dnf, enable docker service, then rerun get_started.sh."
            python_hint = "Install Python tooling: sudo dnf install -y python3 python3-pip"
        else:
            pm = ""
            docker_hint = "Install Docker Engine plus Docker Compose plugin for this Linux distribution."
            python_hint = "Install Python 3.11+ and venv support for this Linux distribution."
        return OSInfo(
            os_family="wsl" if _is_wsl() else "linux",
            label="Windows WSL2" if _is_wsl() else f"Linux {distro}",
            distro=distro,
            arch=arch,
            package_manager=pm,
            is_wsl=_is_wsl(),
            docker_install_hint=docker_hint,
            python_install_hint=python_hint,
        )
    if system == "windows":
        return OSInfo(
            os_family="windows",
            label="Windows",
            arch=arch,
            docker_install_hint="Use WSL2 with Docker Desktop WSL integration, then run ./get_started.sh inside WSL.",
            python_install_hint="Install Python 3.11+ inside WSL.",
        )
    return OSInfo(
        os_family="unsupported",
        label=platform.platform(),
        arch=arch,
        docker_install_hint="Unsupported OS. Use macOS, Linux, or WSL2.",
        python_install_hint="Install Python 3.11+ manually.",
    )


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def command_ok(command: list[str], timeout: int = 10) -> bool:
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout).returncode == 0
    except Exception:
        return False


if __name__ == "__main__":
    import json

    print(json.dumps(detect_os().to_dict(), indent=2))
