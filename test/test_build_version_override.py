import importlib.util
import os
import pathlib
import sys
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
SPEC = importlib.util.spec_from_file_location(
    "gd32_buildcommands", ROOT / "scripts" / "buildcommands.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_explicit_build_version_is_deterministic_and_keeps_extra_suffix():
    with mock.patch.dict(
            os.environ,
            {"KLIPPER_BUILD_VERSION": "gd32-prtouch-v3-source-rebuild"},
            clear=False):
        assert MODULE.build_version("", False) == \
            "gd32-prtouch-v3-source-rebuild"
        assert MODULE.build_version("-extra", True) == \
            "gd32-prtouch-v3-source-rebuild-extra"


def test_tool_version_first_line_handles_carriage_return():
    with mock.patch.object(
            MODULE, "check_output", return_value="gcc 1.2.3\rsecond line\r\n"):
        clean, description = MODULE.tool_versions("gcc;as")
    assert not clean
    assert "second line" not in description
