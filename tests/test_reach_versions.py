"""What ComfyUI runs, read from its /system_stats answer (reach.versions_of)."""

from __future__ import annotations

import json

from comfyui_sandbox.reach import versions_of

# A real answer, from a sandbox's ComfyUI 0.40.0 (2026-10-15), trimmed to the fields that matter.
STATS = json.dumps(
    {
        "system": {
            "os": "linux",
            "comfyui_version": "0.40.0",
            "required_frontend_version": "1.55.17",
            "comfy_package_versions": [
                {"name": "comfyui-frontend-package", "installed": "1.55.17", "required": "1.55.17"},
                {"name": "comfyui-workflow-templates", "installed": "0.11.82", "required": "0.11.82"},
            ],
            "python_version": "3.12.15 (main, Oct  6 2026, 02:02:22) [GCC 14.2.0]",
            "pytorch_version": "2.14.1+cu130",
        }
    }
)


def test_the_versions_are_comfyuis_the_installed_frontends_pytorchs_and_pythons_number():
    assert versions_of(STATS) == {
        "ComfyUI": "0.40.0",
        "Frontend": "1.55.17",
        "PyTorch": "2.14.1+cu130",
        "Python": "3.12.15",
    }


def test_an_older_build_without_the_package_list_has_no_frontend():
    older = json.dumps(
        {
            "system": {
                "os": "posix",
                "comfyui_version": "0.3.9",
                "python_version": "3.12.3",
                "pytorch_version": "2.9.0+cu130",
            }
        }
    )
    assert versions_of(older) == {"ComfyUI": "0.3.9", "PyTorch": "2.9.0+cu130", "Python": "3.12.3"}


def test_an_answer_that_says_nothing_usable_has_no_versions():
    for body in ("not json", "[]", '{"os": "linux"}', '{"system": {"os": "linux", "comfyui_version": 3}}'):
        assert versions_of(body) == {}
