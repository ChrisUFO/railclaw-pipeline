import subprocess
import sys

if sys.platform == "win32":

    def _make_startupinfo():
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return si

    _startupinfo = _make_startupinfo()

    class _QuietPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", 0)
            kwargs["creationflags"] |= subprocess.CREATE_NO_WINDOW
            kwargs.setdefault("startupinfo", _startupinfo)
            super().__init__(*args, **kwargs)

    subprocess.Popen = _QuietPopen
