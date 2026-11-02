"""The window of the debugger: the code at the place where the program stands, the registers, the breakpoints and a
piece of memory, and the keys to go on by a step, over a call, or up to a breakpoint.

What the panels say is made by the plain functions at the top (`register_lines`, `listing`, `hexdump`), which know
nothing of Tk; the window only puts their lines on the screen. The commands are those of debugger.py.

Like the window of the screen (window.py) it is looked after from the one thread of the emulator, and it is a Display
too, so that the monitor that looks after the others looks after it, while the program runs: its `poll()` lets Tk
handle the keys and the buttons. A press of "Stop" only says so (`pause_requested`); the program is stopped by the
`tick` of the command, between two instructions.
"""
import time

from . import breakpoints, flags
from .cpu import CPU
from .debugger import BREAKPOINT, EXIT, PAUSE, Debugger, Pause
from .disasm import disasm
from .display import Display
from .dump import REG16, SEGS
from .window import WindowError

TICK_EVERY = 20_000  # how often a running program looks at the window, in instructions
LISTING_LINES = 24
MEMORY_ROWS = 8
FLAG_LETTERS = ((flags.OF, "O"), (flags.DF, "D"), (flags.IF, "I"), (flags.TF, "T"), (flags.SF, "S"), (flags.ZF, "Z"),
                (flags.AF, "A"), (flags.PF, "P"), (flags.CF, "C"))


def digits(cpu):
    """How many hex digits an offset has here: 8 in the flat mode, 4 in real mode."""
    return 8 if cpu.ip_mask > 0xFFFF else 4


def register_lines(cpu):
    """The registers as lines of text: the general ones (32 bits wide in the flat mode), the segments, the place
    of the next instruction, and the flags, each set one as its letter and each clear one as a dot."""
    width = 8 if cpu.flat else 4
    names = REG16 if not cpu.flat else ["E" + name for name in REG16]
    values = [f"{names[i]}={cpu.wide(i):0{width}X}" for i in range(8)]
    lines = [" ".join(values[i:i + 4]) for i in (0, 4)]
    segments = SEGS if cpu.flat else SEGS[:4]
    lines.append(" ".join(f"{name}={cpu.s[i]:04X}" for i, name in enumerate(segments)))
    lines.append(f"{'EIP' if cpu.flat else 'IP'}={cpu.ip:0{digits(cpu)}X}")
    letters = "".join(letter if cpu.flags & bit else "." for bit, letter in FLAG_LETTERS)
    lines.append(f"FLAGS={cpu.flags:04X} {letters}")
    return lines


def listing(cpu, count=LISTING_LINES):
    """The `count` instructions from the next one on, as (segment, offset, text): the text is the address, the bytes
    and the instruction, as the log of a run writes them."""
    seg, off, found = cpu.s[CPU.CS], cpu.ip, []
    for _ in range(count):
        try:
            length, text = disasm(cpu, seg, off)
            raw = bytes(cpu.rb(seg, off + i) for i in range(length)).hex(" ").upper()
        except Exception:  # the end of what can be read: say so and stop
            found.append((seg, off, f"{seg:04X}:{off:0{digits(cpu)}X}  ??"))
            break
        found.append((seg, off, f"{seg:04X}:{off:0{digits(cpu)}X}  {raw:<20}  {text}"))
        off = (off + length) & cpu.ip_mask
    return found


def hexdump(cpu, seg, off, rows=MEMORY_ROWS):
    """`rows` lines of 16 bytes at seg:off: the address, the bytes in hex (?? for what cannot be read) and as text."""
    lines = []
    for row in range(rows):
        start = (off + row * 16) & 0xFFFFFFFF
        values = []
        for i in range(16):
            try:
                values.append(cpu.rb(seg, start + i))
            except Exception:
                values.append(None)
        hexes = " ".join("??" if v is None else f"{v:02X}" for v in values)
        text = "".join("." if v is None or not 32 <= v < 127 else chr(v) for v in values)
        lines.append(f"{seg:04X}:{start:08X}  {hexes}  {text}")
    return lines


def breakpoint_text(segment, offset):
    """A breakpoint as it is typed: SEGMENT:OFFSET, or the offset alone for any segment."""
    return f"{offset:X}" if segment is None else f"{segment:04X}:{offset:X}"


def stop_text(stop):
    """What a Stop says to a person."""
    if stop.reason == EXIT:
        return f"the program ended with code {stop.code}"
    return {BREAKPOINT: "stopped at a breakpoint", PAUSE: "stopped"}.get(stop.reason, "")


class DebugWindow(Display):
    """The window. `debugger` is the Debugger of the machine; `tick` (optional) is what looks after the other displays
    while the program runs (the monitor's tick: it has this window among them and calls its poll()); without it the
    window looks after itself."""

    def __init__(self, debugger, tick=None, hidden=False):
        """Open the window, the program stopped before its first instruction. `hidden` keeps it off the screen, for
        tests. Raises WindowError when it cannot be done."""
        try:
            import tkinter
        except ImportError:
            raise WindowError("this Python has no tkinter") from None
        self.tkinter = tkinter
        self.debugger = debugger
        self.cpu = debugger.cpu
        self.tick_others = tick
        self.pause_requested = False
        self.running = False
        self.crash = None  # the error that stopped the program, to be raised again when the window is closed
        self.lines = []  # (segment, offset) of the lines of the listing
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError as error:
            raise WindowError(str(error)) from None
        self.root.title("mydos debugger")
        self.build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_delete)
        for key, command in (("<F7>", self.on_step), ("<F8>", self.on_over), ("<F9>", self.on_go),
                             ("<F6>", self.on_stop)):
            self.root.bind(key, lambda event, command=command: command())
        self.refresh()
        if hidden:
            self.root.withdraw()

    def build(self):
        """Make the panels: the buttons on top, the code and the memory on the left, the rest on the right."""
        tk = self.tkinter
        font = "TkFixedFont"
        bar = tk.Frame(self.root)
        bar.grid(row=0, column=0, columnspan=2, sticky="w")
        self.buttons = {}
        for name, text, command in (("step", "Step F7", self.on_step), ("over", "Over F8", self.on_over),
                                    ("go", "Go F9", self.on_go), ("stop", "Stop F6", self.on_stop)):
            self.buttons[name] = tk.Button(bar, text=text, command=command)
            self.buttons[name].pack(side="left")
        self.buttons["stop"].configure(state="disabled")
        self.status = tk.Label(bar, text="", anchor="w")
        self.status.pack(side="left", padx=8)

        self.code = tk.Text(self.root, width=72, height=LISTING_LINES, font=font, state="disabled", cursor="arrow")
        self.code.grid(row=1, column=0, sticky="nsew")
        self.code.tag_configure("current", background="#cde")
        self.code.tag_configure("breakpoint", foreground="#c00")
        self.code.bind("<Double-Button-1>", self.on_code_double_click)

        side = tk.Frame(self.root)
        side.grid(row=1, column=1, rowspan=2, sticky="nsew")
        self.registers = tk.Text(side, width=40, height=7, font=font, state="disabled")
        self.registers.pack(fill="x")
        tk.Label(side, text="Breakpoints (double click on a line of the code puts one)", anchor="w").pack(fill="x")
        self.breakpoint_list = tk.Listbox(side, height=8, font=font)
        self.breakpoint_list.pack(fill="both", expand=True)
        entry_row = tk.Frame(side)
        entry_row.pack(fill="x")
        self.breakpoint_entry = tk.Entry(entry_row, font=font, width=16)
        self.breakpoint_entry.pack(side="left")
        self.breakpoint_entry.bind("<Return>", lambda event: self.on_add_breakpoint())
        tk.Button(entry_row, text="Add", command=self.on_add_breakpoint).pack(side="left")
        tk.Button(entry_row, text="Remove", command=self.on_remove_breakpoint).pack(side="left")

        memory_row = tk.Frame(self.root)
        memory_row.grid(row=2, column=0, sticky="nsew")
        tk.Label(memory_row, text="Memory at").pack(side="left")
        self.memory_entry = tk.Entry(memory_row, font=font, width=16)
        self.memory_entry.insert(0, f"{self.cpu.s[CPU.DS]:04X}:0")
        self.memory_entry.pack(side="left")
        self.memory_entry.bind("<Return>", lambda event: self.refresh())
        self.memory = tk.Text(self.root, width=72, height=MEMORY_ROWS, font=font, state="disabled")
        self.memory.grid(row=3, column=0, columnspan=2, sticky="nsew")

    def put(self, widget, text):
        """Replace everything in a read-only text widget."""
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def refresh(self):
        """Show the machine as it is now: all the panels."""
        stops = set(self.debugger.breakpoints())
        found = listing(self.cpu)
        self.lines = [(seg, off) for seg, off, _ in found]
        marks = [("*" if (seg, off) in stops or (None, off) in stops else " ") + (">" if i == 0 else " ") + " "
                 for i, (seg, off, _) in enumerate(found)]
        self.put(self.code, "\n".join(mark + text for mark, (_, _, text) in zip(marks, found)))
        for i, mark in enumerate(marks):
            if mark[0] == "*":
                self.code.tag_add("breakpoint", f"{i + 1}.0", f"{i + 1}.end")
        self.code.tag_add("current", "1.0", "1.end")
        self.put(self.registers, "\n".join(register_lines(self.cpu)))
        self.breakpoint_list.delete(0, "end")
        for segment, offset in self.debugger.breakpoints():
            self.breakpoint_list.insert("end", breakpoint_text(segment, offset))
        self.refresh_memory()

    def refresh_memory(self):
        """The memory panel, from the address in its entry: SEGMENT:OFFSET, or the offset alone, in the segment DS."""
        try:
            segment, offset = breakpoints.parse(self.memory_entry.get())
        except ValueError as error:
            self.put(self.memory, str(error))
            return
        if segment is None:
            segment = self.cpu.s[CPU.DS]
        self.put(self.memory, "\n".join(hexdump(self.cpu, segment, offset)))

    def say(self, text):
        """The words next to the buttons."""
        self.status.configure(text=text)

    def command(self, run, running=False):
        """Run what a button asks (`run` returns a Stop) and show the result. While a program `running` goes on, only
        Stop is alive. A program that fails stays where it failed: the window shows it, says why, and the error is
        raised again when the window is closed."""
        if self.running or self.crash or self.debugger.exit:
            return
        self.running = True
        for name, button in self.buttons.items():
            button.configure(state="normal" if name == "stop" else "disabled")
        self.pause_requested = False
        try:
            stop = run()
            self.say(stop_text(stop))
        except Exception as error:  # the machine stopped in the middle of something it cannot do
            self.crash = error
            self.say(f"{type(error).__name__}: {error}")
        finally:
            self.running = False
        for name, button in self.buttons.items():
            button.configure(state="disabled" if name == "stop" or self.crash or self.debugger.exit else "normal")
        self.refresh()

    def tick(self):
        """Called by the running program every so often: look after the windows, and stop it if Stop was pressed."""
        (self.tick_others or self.poll)()
        if self.pause_requested:
            raise Pause()

    def on_step(self):
        self.command(self.debugger.step)

    def on_over(self):
        self.command(lambda: self.debugger.step_over(TICK_EVERY, self.tick))

    def on_go(self):
        self.command(lambda: self.debugger.go(TICK_EVERY, self.tick))

    def on_stop(self):
        self.pause_requested = True

    def on_add_breakpoint(self):
        """The breakpoint typed in the entry, for the list."""
        try:
            segment, offset = breakpoints.parse(self.breakpoint_entry.get())
        except ValueError as error:
            self.say(str(error))
            return
        self.debugger.add_breakpoint(segment, offset)
        self.refresh()

    def on_remove_breakpoint(self):
        """The breakpoint that is chosen in the list goes away."""
        for index in self.breakpoint_list.curselection():
            segment, offset = breakpoints.parse(self.breakpoint_list.get(index))
            self.debugger.remove_breakpoint(segment, offset)
        self.refresh()

    def toggle_breakpoint(self, line):
        """A breakpoint at the line (counted from 0) of the listing: put, or taken away if it is there."""
        if line >= len(self.lines):
            return
        segment, offset = self.lines[line]
        if not self.debugger.remove_breakpoint(segment, offset):
            self.debugger.add_breakpoint(segment, offset)
        self.refresh()

    def on_code_double_click(self, event):
        self.toggle_breakpoint(int(self.code.index(f"@{event.x},{event.y}").split(".")[0]) - 1)
        return "break"

    def on_delete(self):
        self.closed = True

    def present(self, frame):
        """The picture of the screen is not for this window."""

    def poll(self):
        if self.closed:
            return
        try:
            self.root.update()
            self.root.winfo_exists()
        except self.tkinter.TclError:  # the window is gone
            self.closed = True

    def close(self):
        self.closed = True
        try:
            self.root.destroy()
        except self.tkinter.TclError:
            pass

    def run(self):
        """Let the person drive the program until the window is closed; returns the exit code of the program (0 if it
        did not end), or raises the error that stopped it."""
        while not self.closed:
            self.poll()
            time.sleep(0.02)  # nothing happens but what the person does
        if self.crash:
            raise self.crash
        return self.debugger.exit.code if self.debugger.exit else 0
