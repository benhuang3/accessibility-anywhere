"""Transient, click-through marker windows. All methods run on the overlay's Tk thread."""

import ctypes
import sys

KEY_COLOR = "#010203"
YELLOW = "#ffd52a"
TIP = "#dca900"
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x08000000


def _click_through(win) -> None:
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = int(win.wm_frame(), 16)
    get = user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.GetWindowLongW
    put = user32.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.SetWindowLongW
    get.argtypes, get.restype = (wintypes.HWND, ctypes.c_int), ctypes.c_ssize_t
    put.argtypes, put.restype = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t), ctypes.c_ssize_t
    style = get(hwnd, -20)
    flags = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    put(hwnd, -20, style | flags)
    if get(hwnd, -20) & flags != flags:
        raise OSError("Marker window is not click-through")


class _Stroke:
    def __init__(self, owner, x, y, ripple=False):
        self.owner = owner
        self.win = win = owner.tk.Toplevel(owner.root)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=KEY_COLOR)
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", KEY_COLOR)
        win.attributes("-alpha", .65 if ripple else .45)
        win.geometry(f"{owner.root.winfo_screenwidth()}x{owner.root.winfo_screenheight()}+0+0")
        self.canvas = owner.tk.Canvas(win, bg=KEY_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.last = (x, y)
        self.tip = None
        self.timer = None
        self.closed = False
        if ripple:
            self.ring = self.canvas.create_oval(x, y, x, y, outline=TIP, width=3 * owner.scale)
        else:
            r = 14 * owner.scale
            self.canvas.create_oval(x-r, y-r, x+r, y+r, fill=YELLOW, outline="")
            self.move(x, y)
        win.update_idletasks()
        try:
            _click_through(win)
            owner.show(win)
            _click_through(win)
        except Exception:
            self.close()
            raise  # never show a window that might intercept the person's clicks
        if ripple:
            self.ripple(x, y, 0)

    def move(self, x, y):
        if self.closed:
            return
        if self.last != (x, y):
            self.canvas.create_line(*self.last, x, y, fill=YELLOW, width=28 * self.owner.scale,
                                    capstyle="round", joinstyle="round")
        if self.tip is not None:
            self.canvas.delete(self.tip)
        r = 4 * self.owner.scale
        self.tip = self.canvas.create_oval(x-r, y-r, x+r, y+r, fill=TIP, outline="")
        self.last = (x, y)

    def end(self, x, y):
        self.move(x, y)
        self.timer = self.win.after(2150, self.fade, 0)

    def fade(self, step):
        if self.closed:
            return
        if step >= 7:
            self.close()
            return
        self.win.attributes("-alpha", .45 * (1 - step / 7))
        self.timer = self.win.after(50, self.fade, step + 1)

    def ripple(self, x, y, step):
        if self.closed:
            return
        if step >= 10:
            self.close()
            return
        r = (8 + step * 3) * self.owner.scale
        self.canvas.coords(self.ring, x-r, y-r, x+r, y+r)
        self.win.attributes("-alpha", .65 * (1 - step / 10))
        self.timer = self.win.after(45, self.ripple, x, y, step + 1)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.win.after_cancel(self.timer)
        self.win.destroy()
        self.owner.windows.discard(self)


class Marker:
    def __init__(self, tk, root, scale, show):
        self.tk, self.root, self.scale, self.show = tk, root, scale, show
        self.current = None
        self.windows = set()

    def handle(self, phase, x, y):
        if phase == "clear":
            for stroke in list(self.windows):
                stroke.close()
            self.current = None
        elif phase in ("start", "ripple"):
            if self.current is not None:
                self.current.end(*self.current.last)
                self.current = None
            stroke = _Stroke(self, x, y, ripple=phase == "ripple")
            self.windows.add(stroke)
            if phase == "start":
                self.current = stroke
        elif phase in ("move", "end") and self.current is not None:
            if phase == "move":
                self.current.move(x, y)
            else:
                self.current.end(x, y)
                self.current = None
