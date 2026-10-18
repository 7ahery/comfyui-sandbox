"""Text too long for its room is cut in the middle, so both ends stay readable: a name's prefix and
suffix, a path's drive and last folders.

Each is checked at its edges: nothing to cut, an exact fit, one over, and very narrow.
"""

import pytest

from comfyui_sandbox.tui.parts import elide
from comfyui_sandbox.tui.run_window.view import elide_path

# -- elide ----------------------------------------------------------------------------------------


def test_eliding_nothing_gives_nothing() -> None:
    assert elide("", 5) == ""


def test_text_that_exactly_fits_is_left_whole() -> None:
    assert elide("abcdef", 6) == "abcdef"


def test_one_over_cuts_the_middle_keeping_more_of_the_end() -> None:
    # The end is what tells similar names apart (comfyui-flux, comfyui-sdxl), so it gets two thirds.
    assert elide("abcdefg", 6) == "ab…efg"


@pytest.mark.parametrize(("width", "want"), [(1, "…"), (2, "a…"), (3, "a…g"), (4, "a…fg")])
def test_very_narrow_still_shows_where_the_cut_is(width: int, want: str) -> None:
    assert elide("abcdefg", width) == want


@pytest.mark.parametrize("width", range(1, 30))
def test_an_elided_name_always_fits(width: int) -> None:
    assert len(elide("flux-dev-experiments-2026-autumn", width)) <= width


# -- elide_path -----------------------------------------------------------------------------------


def test_an_empty_path_stays_empty() -> None:
    assert elide_path("", 10) == ""


def test_a_path_that_exactly_fits_is_left_whole() -> None:
    assert elide_path(r"D:\AI\models", 12) == r"D:\AI\models"


def test_one_over_drops_whole_folders_from_the_middle() -> None:
    assert elide_path(r"D:\AI\models\sdxl", 16) == r"D:\…\models\sdxl"


def test_a_network_share_keeps_its_server_name() -> None:
    path = r"\\nas.local\media\models\stable-diffusion"
    assert elide_path(path, 30) == r"\\nas.local\…\stable-diffusion"


def test_a_wsl_path_is_cut_at_its_forward_slashes() -> None:
    assert elide_path("~/comfyui-models/checkpoints/sdxl", 20) == "~/…/checkpoints/sdxl"


def test_a_last_folder_too_long_by_itself_keeps_the_start_of_its_name() -> None:
    path = "D:\\a\\" + "averyveryverylongfoldername"
    assert elide_path(path, 20) == "D:\\…\\averyveryveryl…"


def test_very_narrow_falls_back_to_cutting_the_middle() -> None:
    # Too little room for the drive, a folder's start and two …: cut it like any other text.
    path = "D:\\a\\" + "averyveryverylongfoldername"
    assert elide_path(path, 8) == "D:\\…name"


@pytest.mark.parametrize(
    "path",
    [
        r"D:\AI\models\sdxl",
        r"\\nas.local\media\models\stable-diffusion\checkpoints",
        "~/comfyui-models/checkpoints/sdxl",
        "D:\\a\\averyveryverylongfoldername",
    ],
)
def test_an_elided_path_always_fits(path: str) -> None:
    for width in range(1, len(path) + 2):
        assert len(elide_path(path, width)) <= width, width
