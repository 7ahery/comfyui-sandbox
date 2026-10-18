"""The TUI sees the world only through its `Gateway`: these drive the screens on the demo gateway
(the spec's seam A) and check what's drawn and which command lines the gateway was asked to run.

The demo gateway is the stand-in a redesign works against, so what's asserted here is what any
gateway promises: the screens draw whatever data it returns, and every action is a command line
the gateway runs and hands back as an operation handle.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import scenarios
from comfyui_sandbox import tui
from comfyui_sandbox.tui.gateway import CheckItem, Sandbox
from comfyui_sandbox.tui.gateway.demo import DemoGateway, Scenario
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.states import SandboxState, StepStatus

TUI = Path(tui.__file__).parent
FAST = Scenario(speed=20)


def home(gateway: DemoGateway, test, size=(120, 30)) -> None:
    """Runs home on gateway and hands test a Rec once its picker is up."""

    async def scenario() -> None:
        app = SandboxApp(gateway)
        async with app.run_test(size=size) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await test(r)

    asyncio.run(scenario())


def bottom_bar(r: scenarios.Rec) -> str:
    return r.text.splitlines()[-1]


# -- Check this PC ------------------------------------------------------------------------------


def test_check_this_pc_draws_the_items_the_gateway_returns() -> None:
    gateway = DemoGateway(
        checks=[CheckItem("Kernel", "6.6.87", ok=True), CheckItem("Free disk on /", "3 GB", ok=False)]
    )

    async def test(r: scenarios.Rec) -> None:
        await r.action("Check this PC")
        await r.wait("Press any key to go back")
        await r.settle()
        lines = r.text.splitlines()
        assert any("✓" in line and "Kernel" in line and "6.6.87" in line for line in lines)
        assert any("✗" in line and "Free disk on /" in line and "3 GB" in line for line in lines)
        for row in ("Windows", "WSL", "Virtualization"):  # the old hard-coded rows aren't drawn
            assert not any(f"  {row}  " in line for line in lines), row
        assert "This PC is ready for an install" not in r.text  # one item failed

    home(gateway, test)


def test_a_failed_wsl_check_offers_to_run_prereqs() -> None:
    gateway = DemoGateway(checks=[CheckItem("WSL", "WSL is not installed.", ok=False, fixable=True)])

    async def test(r: scenarios.Rec) -> None:
        await r.action("Check this PC")
        await r.wait("Set WSL up now?")
        await r.press("enter", settle=0.4)  # Yes is the default
        await r.wait("as administrator?")  # prereqs' own question, with its why
        await r.press("enter")
        await r.wait("Reboot Windows to finish")  # a fresh install: Windows has to restart first
        assert gateway.ran == [("prereqs",)]

    home(gateway, test)


def test_wsl_set_up_without_a_reboot_carries_on_with_the_check() -> None:
    gateway = DemoGateway(
        checks=[CheckItem("WSL", "WSL 2.3.26.0 is too old.", ok=False, fixable=True)],
        scenario=Scenario(speed=20, wsl_reboot=False),
    )

    async def test(r: scenarios.Rec) -> None:
        await r.action("Check this PC")
        await r.wait("Set WSL up now?")
        await r.press("enter", settle=0.4)
        await r.wait("as administrator?")
        await r.press("enter")
        await r.wait("Set WSL up now?")  # no reboot needed: the check runs again, and still fails here
        assert gateway.ran == [("prereqs",)]

    home(gateway, test)


def test_declining_the_wsl_setup_runs_nothing() -> None:
    gateway = DemoGateway(checks=[CheckItem("WSL", "WSL is not installed.", ok=False, fixable=True)])

    async def test(r: scenarios.Rec) -> None:
        await r.action("Check this PC")
        await r.wait("Set WSL up now?")
        await r.press("n", "enter", settle=0.4)
        assert gateway.ran == []

    home(gateway, test)


# -- the GPU meters -----------------------------------------------------------------------------


def test_the_bottom_bar_shows_the_machines_gpu_readings() -> None:
    async def test(r: scenarios.Rec) -> None:
        await r.settle()
        assert "GPU" in bottom_bar(r) and "VRAM" in bottom_bar(r) and "/24 GB" in bottom_bar(r)

    home(DemoGateway(), test)


def test_a_gateway_with_no_gpu_readings_hides_the_meters() -> None:
    async def test(r: scenarios.Rec) -> None:
        await r.settle(1.2)  # a pulse or two: the readings are asked for again
        assert "GPU" not in bottom_bar(r) and "VRAM" not in bottom_bar(r)
        assert "ctrl+c" in bottom_bar(r)  # the keys are still there

    home(DemoGateway(gpu=False), test)


# -- actions are command lines ------------------------------------------------------------------


def test_stop_runs_the_command_line_it_shows_and_the_row_then_reads_stopped() -> None:
    gateway = DemoGateway(
        [Sandbox("alpha", SandboxState.RUNNING, 8188), Sandbox("beta", SandboxState.STOPPED, 8189)], scenario=FAST
    )

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Stop")
        await r.wait("alpha stopped")
        assert gateway.ran == [("stop", "--distro", "alpha")]
        assert any("alpha" in line and "○ stopped" in line for line in r.text.splitlines())

    home(gateway, test)


def test_uninstall_runs_its_command_line_and_the_sandbox_leaves_home() -> None:
    gateway = DemoGateway(
        [Sandbox("alpha", SandboxState.STOPPED, 8188), Sandbox("beta", SandboxState.STOPPED, 8189)], scenario=FAST
    )

    async def test(r: scenarios.Rec) -> None:
        await r.row("beta")
        await r.choose("Uninstall…")
        await r.wait("Type the sandbox name to confirm (beta):")
        await r.press(*"beta", "enter")
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.4)
        await r.wait("Pick a sandbox or an action")
        assert gateway.ran == [("uninstall", "--distro", "beta")]  # it asks for the name itself
        assert not any(" beta " in line for line in r.text.splitlines())

    home(gateway, test)


def test_a_new_sandbox_from_the_install_shows_on_home() -> None:
    gateway = DemoGateway([], scenario=FAST)

    async def test(r: scenarios.Rec) -> None:
        await r.action("Install a new sandbox")
        await scenarios.through(r, "nothing has changed yet")  # every default, up to the review
        await r.press("enter")
        await r.wait("Press any key to go back", timeout=60)
        await r.press("enter", settle=0.4)
        await r.wait("Pick a sandbox or an action")
        assert gateway.ran == [("install", "--new")]  # its questions are its own
        assert any("comfy-docker" in line and "● running" in line for line in r.text.splitlines())

    home(gateway, test)


# -- the operation handle -----------------------------------------------------------------------


def run(gateway: DemoGateway, *argv: str, then=None):
    """Runs argv on gateway and returns its handle once it has ended. then(handle) runs while it's going."""

    async def go():
        handle = gateway.run(list(argv))
        if then is not None:
            await then(handle)
        await handle.wait()
        return handle

    return asyncio.run(go())


def test_a_handle_shows_the_plan_its_steps_and_lines_and_ends_with_a_result() -> None:
    seen = {}

    async def watch(handle) -> None:
        seen["plan"] = [step.title for step in handle.progress.plan]
        while handle.progress.current is None:
            await asyncio.sleep(0.01)
        seen["current"] = handle.progress.current

    handle = run(DemoGateway(scenario=FAST), "backup", "--distro", "comfyui", then=watch)
    result = handle.result
    assert result.exit_code == 0 and result.ok
    assert seen["plan"][0] == "Save the custom-node snapshot" and seen["plan"][1].startswith("Write comfyui-")
    assert seen["current"] == 0
    assert [step.status for step in handle.progress.plan] == [StepStatus.DONE, StepStatus.DONE]
    assert handle.progress.lines  # what the steps printed
    assert result.data["name"] in seen["plan"][1]
    assert result.log is not None and "backup-" in result.log


def test_cancelling_a_handle_ends_it_as_interrupted_at_the_step_it_was_on() -> None:
    async def cancel_soon(handle) -> None:
        while handle.progress.current is None:
            await asyncio.sleep(0.01)
        handle.cancel()

    handle = run(DemoGateway(scenario=Scenario(speed=0.5)), "install", "--yes", "--distro", "new", then=cancel_soon)
    assert handle.result.exit_code == 130
    assert handle.progress.plan[0].status == StepStatus.CANCELLED
    assert all(step.status == StepStatus.PENDING for step in handle.progress.plan[1:])


def test_a_failing_command_ends_with_its_exit_code_and_the_failed_step() -> None:
    handle = run(DemoGateway([], scenario=Scenario(speed=20, fail=True)), "install", "--yes")
    assert not handle.result.ok and handle.result.exit_code == 1
    assert StepStatus.FAILED in [step.status for step in handle.progress.plan]


def test_a_question_waits_for_its_answer() -> None:
    asked = []

    async def answer(handle) -> None:
        # Every question's default, as Enter takes it, until the install has ended.
        while handle.result is None:
            question = handle.progress.question
            if question is not None and question not in asked:
                asked.append(question)
                handle.answer(question.default if question.default is not None else question.choices[0][1])
            await asyncio.sleep(0.01)

    gateway = DemoGateway([], scenario=Scenario(speed=20, port_taken=True))
    handle = run(gateway, "install", then=answer)
    assert handle.result.ok, handle.result.message
    assert asked[-1].message == "Port 8188 is used by python.exe (PID 4120)."

    async def listed():
        return await gateway.sandboxes()

    assert [(s.name, s.port, s.state) for s in asyncio.run(listed())] == [("comfy-docker", 8189, SandboxState.RUNNING)]


# -- the layering ------------------------------------------------------------------------------


def imported_modules(path: Path) -> set[str]:
    """The absolute names of the modules path imports, relative imports resolved."""
    package = ".".join(("comfyui_sandbox", *path.relative_to(TUI.parent).with_suffix("").parts[:-1]))
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")[: len(package.split(".")) - node.level + 1] if node.level else []
            module = ".".join([*base, node.module] if node.module else base)
            names.add(module)
            names |= {f"{module}.{alias.name}" for alias in node.names}
    return names


def test_no_screen_widget_or_flow_imports_the_demo() -> None:
    """Only the demo entry point, which chooses the gateway, and the demo itself may: everything
    else gets a Gateway and knows nothing of what's behind it. (Ticket 04's import-linter
    contracts check the same, and more, for the whole package.)"""
    demo = TUI / "gateway" / "demo"
    hits = []
    for path in sorted(TUI.rglob("*.py")):
        if path == TUI / "demo.py" or demo in path.parents:
            continue
        hits += [
            f"{path.relative_to(TUI)}: {name}"
            for name in imported_modules(path)
            if name.startswith("comfyui_sandbox.tui.gateway.demo")
        ]
    assert not hits, "\n".join(hits)
