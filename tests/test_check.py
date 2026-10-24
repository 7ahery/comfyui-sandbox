"""`comfyui-sandbox check`: read-only machine report (ticket 02).

Each test scripts what Windows would answer (PowerShell, nvidia-smi.exe, wsl.exe)
through the fake Runner and looks at the exit code, the JSON document and stderr.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

GIB = 1024**3


@dataclass
class Machine:
    """What the fake Windows host reports. Defaults: a ready RTX 4090 box."""

    # nvidia-smi.exe --query-gpu rows (name, driver, memory, compute cap); None = no working nvidia-smi
    nvidia_smi: list[tuple[str, str, str, str]] | None = field(
        default_factory=lambda: [("NVIDIA GeForce RTX 4090", "576.80", "24564 MiB", "8.9")]
    )
    driver_cuda_banner: str = "| NVIDIA-SMI 576.80    Driver Version: 576.80    CUDA Version: 12.9     |"
    nvidia_pnp: list[str] = field(default_factory=list)  # NVIDIA PCI devices Windows sees
    video_controllers: list[str] = field(default_factory=lambda: ["NVIDIA GeForce RTX 4090"])
    vmp: int | None = 1  # Win32_OptionalFeature.InstallState: 1 enabled, 2 disabled, 3 absent
    hypervisor: bool | None = True
    firmware: bool | None = False  # reads False whenever a hypervisor runs
    wsl_installed: bool = True
    wsl_version: str = "WSL version: 2.6.1.0\nKernel version: 6.6.87.2-1\n"
    wsl_version_rc: int = 0
    wsl_online: str = (
        "NAME            FRIENDLY NAME\nUbuntu-24.04    Ubuntu 24.04 LTS\nUbuntu-26.04    Ubuntu 26.04 LTS\n"
    )
    distros: list[str] = field(default_factory=lambda: ["Ubuntu", "comfy-docker"])
    ram_bytes: int = 64 * GIB
    cpus: int = 24
    drives: list[tuple[str, int]] = field(default_factory=lambda: [("C:", 120 * GIB), ("D:", 800 * GIB)])

    def script(self, runner, environ) -> None:
        if self.wsl_installed:
            wsl = Path(environ["ProgramFiles"]) / "WSL" / "wsl.exe"
            wsl.parent.mkdir(parents=True, exist_ok=True)
            wsl.write_bytes(b"")
        probe = {
            "vmp": self.vmp,
            "hypervisor": self.hypervisor,
            "firmware": self.firmware,
            "ram_bytes": self.ram_bytes,
            "cpus": self.cpus,
            "drives": [{"id": d, "free_bytes": free} for d, free in self.drives],
            "video": self.video_controllers,
            "nvidia_pnp": self.nvidia_pnp,
        }
        runner.on("powershell.exe", stdout=json.dumps(probe))
        if self.nvidia_smi is None:
            runner.on("nvidia-smi.exe", returncode=9, stderr="NVIDIA-SMI has failed")
        else:
            runner.on("nvidia-smi.exe", stdout=self.driver_cuda_banner + "\n")
            rows = "\n".join(", ".join(row) for row in self.nvidia_smi)
            runner.on(
                "nvidia-smi.exe", "--query-gpu=name,driver_version,memory.total,compute_cap", stdout=rows + "\r\n"
            )
        runner.on("wsl.exe", "--version", returncode=self.wsl_version_rc, stdout=self.wsl_version)
        runner.on("wsl.exe", "--list", "--online", stdout=self.wsl_online)
        runner.on("wsl.exe", "--list", "--quiet", stdout="\n".join(self.distros) + "\n")


@pytest.fixture
def machine():
    return Machine()


@pytest.fixture
def check(cli, runner, environ, machine):
    def run(*extra: str, platform: str = "win32"):
        machine.script(runner, environ)
        return cli("check", "--json", *extra, platform=platform)

    return run


def test_ready_nvidia_machine_exits_0_and_reports_the_gpu(check):
    result = check()

    assert result.exit_code == 0
    data = result.json["data"]
    assert data["gpu"]["mode"] == "nvidia"
    assert data["gpu"]["gpus"] == [
        {"name": "NVIDIA GeForce RTX 4090", "driver": "576.80", "vram_mib": 24564, "compute_capability": "8.9"}
    ]
    assert data["gpu"]["driver_cuda"] == "12.9"
    assert data["gpu"]["cuda_version_suggested"] is None
    assert "NVIDIA GeForce RTX 4090" in result.stderr


RTX_5090 = [("NVIDIA GeForce RTX 5090", "576.80", "32607 MiB", "12.0")]


@pytest.mark.parametrize(
    "banner",
    [
        "| NVIDIA-SMI 576.80    Driver Version: 576.80    CUDA Version: 12.9     |",
        "| NVIDIA-SMI 590.10    Driver Version: 590.10    CUDA UMD Version: 13.1     |",
    ],
)
def test_blackwell_card_gets_cuda_12_8_suggested(check, machine, banner):
    machine.nvidia_smi = RTX_5090
    machine.driver_cuda_banner = banner

    result = check()

    assert result.exit_code == 0
    assert result.json["data"]["gpu"]["cuda_version_suggested"] == "12.8"
    assert "Blackwell" in result.stderr


def test_driver_too_old_for_a_blackwell_card_warns_to_update_the_driver(check, machine):
    machine.nvidia_smi = RTX_5090
    machine.driver_cuda_banner = "| NVIDIA-SMI 566.03    Driver Version: 566.03    CUDA Version: 12.7     |"

    result = check()

    assert result.json["data"]["gpu"]["cuda_version_suggested"] is None
    assert "update the Windows NVIDIA driver" in result.stderr
    assert any("update the Windows NVIDIA driver" in w for w in result.json["data"]["warnings"])


def test_no_nvidia_gpu_exits_3_and_lists_the_gpus_windows_reports(check, machine):
    machine.nvidia_smi = None
    machine.video_controllers = ["AMD Radeon RX 7900 XTX", "AMD Radeon(TM) Graphics"]

    result = check()

    assert result.exit_code == 3
    doc = result.json
    assert doc["data"]["gpu"]["mode"] == "none"
    assert doc["data"]["gpu"]["windows_gpus"] == ["AMD Radeon RX 7900 XTX", "AMD Radeon(TM) Graphics"]
    assert "AMD Radeon RX 7900 XTX" in result.stderr
    assert "No NVIDIA GPU" in doc["message"]


@pytest.mark.parametrize("pnp_name", ["Microsoft Basic Display Adapter", "NVIDIA GeForce RTX 4090"])
def test_nvidia_gpu_without_a_working_driver_exits_6_and_points_to_the_download(check, machine, pnp_name):
    machine.nvidia_smi = None
    machine.nvidia_pnp = [pnp_name]
    machine.video_controllers = [pnp_name]

    result = check()

    assert result.exit_code == 6
    doc = result.json
    assert doc["data"]["gpu"]["mode"] == "nodriver"
    assert doc["data"]["gpu"]["windows_gpus"] == [pnp_name]
    assert "https://www.nvidia.com/drivers" in doc["message"]


def problem_codes(doc) -> set[int]:
    return {p["exit_code"] for p in doc["data"]["problems"]}


def test_virtualization_off_exits_5_and_still_reports_the_other_failures(check, machine):
    machine.hypervisor = False
    machine.firmware = False
    machine.vmp = 2
    machine.nvidia_smi = None

    result = check()

    assert result.exit_code == 5
    doc = result.json
    assert "VT-x" in doc["message"] and "SVM" in doc["message"]
    assert problem_codes(doc) == {5, 7, 3}
    assert doc["data"]["virtualization"] == {
        "hypervisor": False,
        "firmware_enabled": False,
        "virtual_machine_platform": "disabled",
    }
    assert "Virtual Machine Platform is off" in result.stderr


def wsl_calls(runner):
    return [c.argv for c in runner.calls if c.argv[0].lower() == "wsl.exe"]


def test_wsl_not_installed_exits_7_without_ever_calling_wsl_exe(check, machine, runner):
    machine.wsl_installed = False

    result = check()

    assert result.exit_code == 7
    assert "comfyui-sandbox prereqs" in result.json["message"]
    assert result.json["data"]["wsl"]["installed"] is False
    assert wsl_calls(runner) == []


def test_wsl_kernel_older_than_6_6_exits_7_even_when_the_package_is_new_enough(check, machine):
    machine.wsl_version = "WSL version: 2.4.10.0\nKernel version: 5.15.167.4-1\n"

    result = check()

    assert result.exit_code == 7
    assert "5.15.167.4" in result.stderr and "6.6" in result.stderr
    assert "comfyui-sandbox prereqs" in result.json["message"]
    wsl = result.json["data"]["wsl"]
    assert wsl["version"] == "2.4.10.0"
    assert wsl["kernel"] == "5.15.167.4"


def test_wsl_that_reports_no_kernel_warns_without_blocking(check, machine):
    machine.wsl_version = "WSL version: 2.6.1.0\n"

    result = check()

    assert result.exit_code == 0
    assert "not verified" in result.stderr
    assert result.json["data"]["wsl"]["kernel"] is None


def test_wsl_older_than_2_4_4_exits_7(check, machine):
    machine.wsl_version = "WSL-Version: 2.4.3.0\nKernelversion: 6.6.36.6-1\n"

    result = check()

    assert result.exit_code == 7
    assert "2.4.3" in result.stderr and "too old" in result.stderr
    assert "comfyui-sandbox prereqs" in result.json["message"]


def test_broken_wsl_exits_7(check, machine):
    machine.wsl_version_rc = 1
    machine.wsl_version = "Error code: Wsl/Service/0x8007273d\n"

    result = check()

    assert result.exit_code == 7
    assert "wsl --version" in result.stderr


def test_virtual_machine_platform_off_exits_7_even_with_no_gpu(check, machine):
    machine.vmp = 3
    machine.nvidia_smi = None

    result = check()

    assert result.exit_code == 7
    assert problem_codes(result.json) == {7, 3}


def test_ready_wsl_reports_version_ubuntu_and_existing_distros(check, runner):
    result = check()

    wsl = result.json["data"]["wsl"]
    assert wsl == {
        "installed": True,
        "version": "2.6.1.0",
        "kernel": "6.6.87.2",
        "ubuntu_26_04_available": True,
        "distros": ["Ubuntu", "comfy-docker"],
    }
    assert all(c.env.get("WSL_UTF8") == "1" for c in runner.calls if c.argv[0] == "wsl.exe")


def test_hypervisor_not_running_after_a_fresh_wsl_install_warns_to_reboot(check, machine):
    machine.hypervisor = False
    machine.firmware = True

    result = check()

    assert result.exit_code == 0
    assert any("No hypervisor running" in w for w in result.json["data"]["warnings"])


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_non_windows_host_exits_4_and_runs_nothing(check, runner, platform):
    result = check(platform=platform)

    assert result.exit_code == 4
    assert "unsupported host" in result.json["message"].lower()
    assert runner.calls == []


def test_unsupported_host_wins_over_every_other_failure(check, machine):
    machine.wsl_installed = False
    machine.firmware = machine.hypervisor = False
    machine.nvidia_smi = None

    assert check(platform="linux").exit_code == 4


def test_reports_drives_free_space_and_windows_user(check, home):
    data = check().json["data"]

    assert data["drives"] == [{"drive": "C:", "free_gb": 120}, {"drive": "D:", "free_gb": 800}]
    assert data["c_free_gb"] == 120
    assert data["user_profile"] == str(home)
    assert data["win_user_suggested"] == "alex"


def test_windows_user_that_is_not_a_valid_linux_name_is_not_suggested(check, environ):
    environ["USERNAME"] = "Jean Dupont"

    assert check().json["data"]["win_user_suggested"] is None


@pytest.mark.parametrize(
    ("ram_gb", "cpus", "limits"),
    [
        # worked examples from wslconfig.sh: memory, "more room for Windows", swap
        (
            16,
            8,
            {"host_ram_gb": 16, "host_cpus": 8, "memory_gb": 12, "memory_low_gb": 8, "processors": 8, "swap_gb": 4},
        ),
        (
            32,
            16,
            {"host_ram_gb": 32, "host_cpus": 16, "memory_gb": 24, "memory_low_gb": 16, "processors": 16, "swap_gb": 4},
        ),
        (
            64,
            24,
            {"host_ram_gb": 64, "host_cpus": 24, "memory_gb": 56, "memory_low_gb": 48, "processors": 24, "swap_gb": 8},
        ),
        (
            128,
            32,
            {
                "host_ram_gb": 128,
                "host_cpus": 32,
                "memory_gb": 120,
                "memory_low_gb": 112,
                "processors": 32,
                "swap_gb": 16,
            },
        ),
        (8, 4, {"host_ram_gb": 8, "host_cpus": 4, "memory_gb": 4, "memory_low_gb": 4, "processors": 4, "swap_gb": 4}),
    ],
)
def test_suggests_wsl_limits_from_the_hardware(check, machine, ram_gb, cpus, limits):
    machine.ram_bytes = ram_gb * GIB - 300 * 1024**2  # Windows reports a little less than the label
    machine.cpus = cpus

    assert check().json["data"]["wsl_limits"] == limits


def test_check_is_read_only_and_writes_no_transcript(check, runner, home):
    result = check()

    assert result.json["exit_code"] == 0
    assert "log" not in result.json
    assert list(home.iterdir()) == []
    assert [c.argv[0] for c in runner.calls] and all(
        c.argv[:2] not in {("wsl.exe", "--install"), ("wsl.exe", "--shutdown")} for c in runner.calls
    )


def test_without_json_the_report_goes_to_stderr_and_the_summary_to_stdout(cli, runner, environ, machine):
    machine.script(runner, environ)

    result = cli("check")

    assert result.exit_code == 0
    assert result.stdout == "This machine is ready.\n"
    assert "== GPU ==" in result.stderr and "== WSL ==" in result.stderr
