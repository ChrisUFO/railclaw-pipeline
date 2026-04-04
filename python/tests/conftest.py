import subprocess
import sys

if sys.platform == "win32":
    _original_run = subprocess.run
    _original_popen = subprocess.Popen

    _NO_WINDOW = subprocess.CREATE_NO_WINDOW

    def _quiet_run(*args, **kwargs):
        kwargs.setdefault("creationflags", 0)
        kwargs["creationflags"] |= _NO_WINDOW
        return _original_run(*args, **kwargs)

    def _quiet_popen(*args, **kwargs):
        kwargs.setdefault("creationflags", 0)
        kwargs["creationflags"] |= _NO_WINDOW
        return _original_popen(*args, **kwargs)

    subprocess.run = _quiet_run
    subprocess.Popen = _quiet_popen
