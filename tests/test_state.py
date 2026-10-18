"""installs.json read/write through the `state` module's public interface (docs/scripting.md, "installs.json")."""

import json

from comfyui_sandbox.state import Gpu, Install, Installs, Models


def make(distro: str) -> Install:
    return Install(
        distro=distro,
        linux_user="comfy",
        port=8188,
        models=Models("wsl", "/home/comfy/comfyui-models"),
        gpu=Gpu("cpu"),
        deployed_by="1.0.0",
    )


def test_a_missing_file_means_no_installs(tmp_path):
    installs = Installs.load(tmp_path)

    assert installs.all() == []
    assert installs.exists is False


def test_saved_installs_load_back(tmp_path):
    installs = Installs.load(tmp_path)
    installs.put(make("comfy-docker"))
    installs.save()

    raw = (tmp_path / "installs.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM
    assert b"\r\n" not in raw
    again = Installs.load(tmp_path)
    assert [i.distro for i in again.all()] == ["comfy-docker"]
    found = again.find("COMFY-DOCKER")
    assert found is not None and found.gpu == Gpu("cpu", None)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "installs.json",
        "installs.json.lock",
    ]  # no temporary file left


def test_put_replaces_the_install_with_the_same_name(tmp_path):
    installs = Installs.load(tmp_path)
    installs.put(make("comfy-docker"))
    updated = make("Comfy-Docker")
    updated.state = "complete"
    installs.put(updated)

    assert [(i.distro, i.state) for i in installs.all()] == [("Comfy-Docker", "complete")]


def test_unknown_keys_survive_a_rewrite(tmp_path):
    record = make("comfy-docker").to_json() | {"future_field": {"x": 1}}
    (tmp_path / "installs.json").write_text(
        json.dumps({"schema": 1, "installs": [record], "future_top": True}), "utf-8"
    )

    installs = Installs.load(tmp_path)
    existing = installs.find("comfy-docker")
    assert existing is not None
    installs.put(existing)
    installs.save()

    raw = json.loads((tmp_path / "installs.json").read_text("utf-8"))
    assert raw["future_top"] is True
    assert raw["installs"][0]["future_field"] == {"x": 1}


def test_remove(tmp_path):
    installs = Installs.load(tmp_path)
    installs.put(make("comfy-docker"))

    assert installs.remove("COMFY-docker") is True
    assert installs.remove("comfy-docker") is False
    assert installs.all() == []


def test_a_save_keeps_a_change_another_command_made_since_the_load(tmp_path):
    seed = Installs.load(tmp_path)
    seed.put(make("keep-me"))
    seed.put(make("remove-me"))
    seed.save()
    install_run = Installs.load(tmp_path)  # `install` loads, then runs for up to an hour

    other = Installs.load(tmp_path)  # meanwhile `uninstall remove-me`
    other.remove("remove-me")
    other.save()
    install_run.put(make("new-one"))
    install_run.save()

    assert sorted(i.distro for i in Installs.load(tmp_path).all()) == ["keep-me", "new-one"]


def test_a_save_applies_only_its_own_removal(tmp_path):
    seed = Installs.load(tmp_path)
    seed.put(make("a"))
    seed.put(make("b"))
    seed.save()
    uninstall_run = Installs.load(tmp_path)

    other = Installs.load(tmp_path)
    other.put(make("c"))
    other.save()
    uninstall_run.remove("a")
    uninstall_run.save()

    assert sorted(i.distro for i in Installs.load(tmp_path).all()) == ["b", "c"]


def test_the_object_reflects_the_saved_file_after_a_save(tmp_path):
    first = Installs.load(tmp_path)
    first.put(make("a"))
    first.save()
    stale = Installs.load(tmp_path)
    other = Installs.load(tmp_path)
    other.put(make("b"))
    other.save()
    stale.put(make("c"))
    stale.save()

    assert sorted(i.distro for i in stale.all()) == ["a", "b", "c"]


def test_a_save_waits_for_the_lock_held_by_another_save(tmp_path):
    import threading
    import time

    from comfyui_sandbox import state

    first = Installs.load(tmp_path)
    first.put(make("a"))
    done = threading.Event()
    with state.locked(tmp_path / "installs.json"):
        worker = threading.Thread(target=lambda: (first.save(), done.set()))
        worker.start()
        time.sleep(0.3)
        assert not done.is_set()
    worker.join(timeout=10)
    assert done.is_set()
    assert [i.distro for i in Installs.load(tmp_path).all()] == ["a"]


# -- the save path reads the file too: it must be no worse than load was --------


def test_a_save_over_a_file_that_became_unreadable_fails_clearly_and_leaves_it_alone(tmp_path):
    import pytest

    from comfyui_sandbox import state
    from comfyui_sandbox.state import StateError

    mine = Installs.load(tmp_path)  # the file was fine (here: missing) at load
    mine.put(make("a"))
    (tmp_path / "installs.json").write_text("{ not json", "utf-8")  # then something broke it

    with pytest.raises(StateError, match=r"can't be read.*never overwritten"):
        mine.save()

    assert (tmp_path / "installs.json").read_text("utf-8") == "{ not json"
    assert list(tmp_path.glob(".installs-*")) == []  # no stray temporary file
    with state.locked(tmp_path / "installs.json"):  # the lock was released
        pass


def test_load_and_save_report_a_broken_file_the_same_way(tmp_path):
    import pytest

    from comfyui_sandbox.state import StateError

    mine = Installs.load(tmp_path)
    mine.put(make("a"))
    (tmp_path / "installs.json").write_text('{"schema": 1, "installs": [{"distro": "x"}]}', "utf-8")

    with pytest.raises(StateError) as on_load:
        Installs.load(tmp_path)
    with pytest.raises(StateError) as on_save:
        mine.save()

    assert on_save.value.message == on_load.value.message


def test_a_save_over_a_file_a_newer_cli_rewrote_fails_clearly_and_leaves_it_alone(tmp_path):
    import pytest

    from comfyui_sandbox import state
    from comfyui_sandbox.state import StateError

    mine = Installs.load(tmp_path)
    mine.put(make("a"))
    newer = json.dumps({"schema": 99, "installs": []})
    (tmp_path / "installs.json").write_text(newer, "utf-8")

    with pytest.raises(StateError, match="newer comfyui-sandbox"):
        mine.save()

    assert (tmp_path / "installs.json").read_text("utf-8") == newer
    with state.locked(tmp_path / "installs.json"):
        pass


def test_a_failed_save_keeps_the_changes_so_the_caller_can_retry(tmp_path):
    import pytest

    from comfyui_sandbox.state import StateError

    mine = Installs.load(tmp_path)
    mine.put(make("a"))
    (tmp_path / "installs.json").write_text("[]", "utf-8")
    with pytest.raises(StateError):
        mine.save()

    (tmp_path / "installs.json").unlink()
    mine.save()

    assert [i.distro for i in Installs.load(tmp_path).all()] == ["a"]
