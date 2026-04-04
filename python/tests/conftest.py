import subprocess
import sys

if sys.platform == "win32":
    _STARTF_USESHOWWINDOW = 1
    _SW_HIDE = 0

    class _QuietPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", 0)
            kwargs["creationflags"] |= subprocess.CREATE_NO_WINDOW

            si = kwargs.get("startupinfo")
            if si is None:
                si = subprocess.STARTUPINFO()
                kwargs["startupinfo"] = si
            si.dwFlags |= _STARTF_USESHOWWINDOW
            si.wShowWindow = _SW_HIDE

            super().__init__(*args, **kwargs)

    subprocess.Popen = _QuietPopen
