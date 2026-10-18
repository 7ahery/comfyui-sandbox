"""A folder in a sandbox is written as a folder glyph and its name, and a click opens it."""

import sys

import pytest
from rich.console import Console
from rich.text import Text

from comfyui_sandbox.tui.home.flows import backups_folder, folder, models_folder


def test_a_folder_shows_a_glyph_and_its_name_and_links_to_the_folder() -> None:
    text = folder("comfyui-2", "user")
    assert text.plain == "\N{FILE FOLDER} user"
    link = text.get_style_at_offset(Console(), 0).link
    assert link is not None
    assert link.endswith("/comfyui-docker/data/ComfyUI/user")
    assert link.startswith("file:")


@pytest.mark.skipif(sys.platform != "win32", reason="the \\wsl.localhost share is Windows's")
def test_on_windows_the_link_goes_through_the_distro_share() -> None:
    link = folder("comfyui-2", "input").get_style_at_offset(Console(), 0).link
    assert link is not None
    assert "wsl.localhost/comfyui-2/home/comfy/comfyui-docker/data/ComfyUI/input" in link


def link_of(text: Text) -> str:
    link = text.get_style_at_offset(Console(), 0).link
    assert link is not None
    return link


def test_a_models_folder_in_the_distro_links_to_it() -> None:
    text = models_folder("comfyui-2", "~/comfyui-models")
    assert text.plain == "\N{FILE FOLDER} ~/comfyui-models"
    assert link_of(text).endswith("/home/comfy/comfyui-models")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
def test_a_models_folder_on_windows_links_to_the_drive_or_share() -> None:
    assert link_of(models_folder("x", r"D:\AI\models")) == "file:///D:/AI/models"
    assert link_of(models_folder("x", r"\\nas.local\media\models")) == "file://nas.local/media/models"


def test_the_backups_folder_links_to_comfyui_backups() -> None:
    text = backups_folder("comfyui-2")
    assert text.plain == "\N{FILE FOLDER} ~/comfyui-backups"
    assert link_of(text).endswith("/home/comfy/comfyui-backups")


def test_a_models_folder_given_as_an_absolute_linux_path_links_to_it() -> None:
    # What a WSL install records (state.Models: an absolute Linux path); it crashed the page once
    # it was handed to the Windows branch, which can't make a file URI of it.
    text = models_folder("comfyui-2", "/home/comfy/comfyui-models")
    assert link_of(text).endswith("/home/comfy/comfyui-models")


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are Windows's")
def test_a_distro_link_names_the_unc_path_so_the_click_does_not_take_it_for_missing() -> None:
    # The click checks the path exists before opening it; the URI's path alone ("/Ubuntu/home")
    # lost the wsl.localhost host, so every WSL link rang the bell instead of opening.
    from comfyui_sandbox.tui.links import distro_uri, file_uri_path

    assert str(file_uri_path(distro_uri("Ubuntu", "/home/comfy"))) == "\\\\" + r"wsl.localhost\Ubuntu\home\comfy"
    assert str(file_uri_path("file:///D:/AI/models")) == r"D:\AI\models"
