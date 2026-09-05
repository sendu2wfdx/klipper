import pathlib
import shutil
import subprocess
import tempfile

import pytest


ROOT = pathlib.Path(__file__).parents[1]


def test_creality_485_slave_c_state_machine():
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("没有可用的宿主 C 编译器")
    with tempfile.TemporaryDirectory() as temp_dir:
        executable = pathlib.Path(temp_dir) / "creality_485_slave_test"
        subprocess.run([
            compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-I", str(ROOT / "src"),
            str(ROOT / "src" / "creality_485_codec.c"),
            str(ROOT / "src" / "creality_485_slave.c"),
            str(ROOT / "test" / "creality_485_slave_test.c"),
            "-o", str(executable),
        ], check=True)
        subprocess.run([str(executable)], check=True)
