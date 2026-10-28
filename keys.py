"""Read single key presses on Windows, in both classic consoles and VT terminals.

Classic consoles send arrows as a two-character code ("\\xe0" + "M"); terminals
like Windows Terminal or VS Code send escape sequences ("\\x1b[C"). Both are
mapped to the same names here.
"""
import msvcrt
import time

_CLASSIC = {"M": "right", "K": "left", "H": "up", "P": "down"}
_VT = {"C": "right", "D": "left", "A": "up", "B": "down"}


def _next_char(timeout=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if msvcrt.kbhit():
            return msvcrt.getwch()
        time.sleep(0.002)
    return None


def read_key():
    """Block until a key is pressed. Returns 'left', 'right', 'up', 'down', 'space', 'esc', or the character."""
    k = msvcrt.getwch()
    if k in ("\x00", "\xe0"):
        return _CLASSIC.get(msvcrt.getwch(), "")
    if k == "\x1b":
        if _next_char() in ("[", "O"):
            return _VT.get(_next_char(), "")
        return "esc"
    if k == " ":
        return "space"
    return k.lower()
