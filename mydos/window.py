"""A window of the host that shows the screen of the emulated machine, and passes the keys typed in it on.

It is made with Tk, which comes with Python on most machines. Tk is only loaded when a window is asked for, so the
emulator runs without it, with no window, on a machine that has none.

The mouse moving over the picture and its buttons go to `pointer(x, y, buttons)`, with the place as a pixel of the
screen of the machine and the buttons as a mask: bit 0 the left one, bit 1 the right one, bit 2 the middle one.

The window opens at the size of the screen of the machine and can be made bigger. The picture grows with it by whole
numbers, 2 times, 3 times and so on, so that its pixels stay sharp, and sits in the middle with black round it.

The window is looked after by the emulator itself, from the one thread it has: `poll()` lets Tk handle what has
happened to the window since the last time (a key pressed, the window closed), and `present()` puts a new picture
in it. Between the calls the window does nothing, so `poll()` has to be called often for it to feel alive.
"""
from . import keys
from .display import Display
from .screen import HEIGHT, WIDTH


class WindowError(Exception):
    """A window cannot be opened. The message says why: no Tk in this Python, or no screen to open it on."""


def zoom_for(width, height, picture_width, picture_height):
    """How many times the picture can be made bigger to fit a window of this size: a whole number, 1 at least."""
    return max(1, min(width // picture_width, height // picture_height))


class TkWindow(Display):
    """A window with the picture in it. Every key pressed goes to `press(scan_code, ascii_code)`, what the mouse does
    to `pointer(x, y, buttons)`, if there is one."""

    # the button numbers of Tk (1 left, 2 middle, 3 right) and their bits in a mask
    BUTTON_BITS = {1: 0b001, 3: 0b010, 2: 0b100}

    def __init__(self, press, pointer=None, title="mydos", hidden=False):
        """Open the window. `hidden` keeps it off the screen, for tests. Raises WindowError when it cannot be done."""
        try:
            import tkinter
        except ImportError:
            raise WindowError("this Python has no tkinter") from None
        self.press = press
        self.pointer = pointer
        self.buttons = 0  # the buttons that are down
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError as error:
            raise WindowError(str(error)) from None
        self.root.title(title)
        self.root.minsize(WIDTH, HEIGHT)  # never smaller than the screen of the machine
        self.canvas = tkinter.Canvas(self.root, width=WIDTH, height=HEIGHT, borderwidth=0, highlightthickness=0,
                                     background="black")
        self.canvas.pack(fill="both", expand=True)
        self.item = self.canvas.create_image(WIDTH // 2, HEIGHT // 2, anchor="center")
        self.root.bind("<KeyPress>", self.on_key)
        self.canvas.bind("<Configure>", lambda event: self.layout(event.width, event.height))
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<ButtonPress>", lambda event: self.on_button(event, True))
        self.canvas.bind("<ButtonRelease>", lambda event: self.on_button(event, False))
        self.root.protocol("WM_DELETE_WINDOW", self.on_delete)
        self.image = None  # the picture as it is, one Tk pixel to a pixel of the machine
        self.shown = None  # the picture as it is in the window, made bigger: Tk throws away one nobody holds on to
        self.zoom = 1
        self.size = (WIDTH, HEIGHT)  # of the area for the picture
        self.tcl_error = tkinter.TclError
        if hidden:
            self.root.withdraw()
        else:
            self.root.focus_force()

    def on_key(self, event):
        key = keys.to_bios(event.keysym, event.char)
        if key is not None:
            self.press(*key)

    def place_of(self, x, y):
        """The pixel of the screen of the machine that is under the point x, y of the window. A point beside the
        picture is taken to the nearest pixel."""
        width, height = (self.image.width(), self.image.height()) if self.image else (WIDTH, HEIGHT)
        left = self.size[0] // 2 - width * self.zoom // 2
        top = self.size[1] // 2 - height * self.zoom // 2
        return (min(max((x - left) // self.zoom, 0), width - 1), min(max((y - top) // self.zoom, 0), height - 1))

    def on_motion(self, event):
        if self.pointer is not None:
            self.pointer(*self.place_of(event.x, event.y), self.buttons)

    def on_button(self, event, down):
        bit = self.BUTTON_BITS.get(event.num)
        if bit is None:  # the wheel, and buttons that a mouse of the PC does not have
            return
        self.buttons = self.buttons | bit if down else self.buttons & ~bit
        if self.pointer is not None:
            self.pointer(*self.place_of(event.x, event.y), self.buttons)

    def on_delete(self):
        self.closed = True

    def layout(self, width, height):
        """The area for the picture is now width by height: choose the zoom, put the picture in the middle."""
        self.size = (width, height)
        self.canvas.coords(self.item, width // 2, height // 2)
        if self.image is not None:
            self.refresh()

    def refresh(self):
        """Make the picture in the window from the picture as it is, for the zoom the size of the window allows."""
        self.zoom = zoom_for(*self.size, self.image.width(), self.image.height())
        self.shown = self.image.zoom(self.zoom) if self.zoom > 1 else self.image
        self.canvas.itemconfigure(self.item, image=self.shown)

    def present(self, frame):
        # Tk reads a picture in the PPM format straight from bytes: a header, then three bytes a pixel
        ppm = b"P6\n%d %d\n255\n" % (frame.width, frame.height) + frame.rgb
        if self.image is None:
            import tkinter
            self.image = tkinter.PhotoImage(data=ppm)
        else:
            self.image.configure(data=ppm)
        self.refresh()

    def poll(self):
        if self.closed:
            return
        try:
            self.root.update()
            self.root.winfo_exists()  # update() says nothing about a window that is gone, this one does
        except self.tcl_error:  # the window is gone: closed from outside, or the display went away
            self.closed = True

    def close(self):
        self.closed = True
        try:
            self.root.destroy()
        except self.tcl_error:
            pass  # already destroyed
