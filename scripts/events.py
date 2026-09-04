"""The seam between the scanner and whatever drives it (console or GUI).

The engine never prints, prompts or exits: it reports through an Events sink
and returns a result. The console driver wires the callbacks to print(); the
GUI wires them to its worker queue. Every callback defaults to a harmless
no-op, so Events() is a valid silent sink.
"""


def _noop_log(message):
    pass


def _noop_progress(done, total, current):
    pass


def _never():
    return False


class Events:
    """Callbacks the scanner uses to report progress and check for a cancel.

    on_log(message):                a human-readable status line.
    on_progress(done, total, current): files finished so far, the total, and
                                    the file being read now (a path string).
    is_cancelled():                 return True to stop before the next file.
    """

    def __init__(self, on_log=None, on_progress=None, is_cancelled=None):
        self.on_log = on_log or _noop_log
        self.on_progress = on_progress or _noop_progress
        self.is_cancelled = is_cancelled or _never
