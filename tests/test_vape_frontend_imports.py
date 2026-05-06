import subprocess
import sys


def test_vape_server_import_does_not_require_local_audio_backend():
    code = """
import importlib.abc
import sys


class BlockLocalAudio(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"soundcard", "sounddevice"}:
            raise AssertionError(f"{fullname} should be imported lazily")
        return None


sys.meta_path.insert(0, BlockLocalAudio())
import linux_voice_assistant.__main__ as main
assert callable(main.run_vape_server_frontend)
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
