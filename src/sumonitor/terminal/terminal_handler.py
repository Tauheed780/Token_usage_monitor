### Manages the terminal and displays usage data for current session

import sys, fcntl, termios, struct, os
import time, threading

from ..data.log_reader import LogReader
from ..session.session_data import SessionData

class TerminalHandler:
    """Handler for managing terminal and drawing overlays"""
    
    def __init__(self, log_reader: LogReader, pexpect_obj, plan: str = "pro") -> None:
        self.in_alt_screen = False # to know when to draw in terminal
        self.p = pexpect_obj
        self.log_reader = log_reader
        self.plan = plan
        self.output_lock = threading.Lock()
        self.real_os_write = os.write
        os.write = self.locked_os_write
        self.overlay_thread = threading.Thread(target=self.draw_overlay, daemon=True)
        self.overlay_thread.start()

    def get_terminal_size(self) -> int:
        """Get terminal size

            Returns: 
                rows, columns -- terminal dimensions
        """
        s = struct.pack("HHHH", 0, 0, 0, 0)
        a = struct.unpack('hhhh', fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, s))
        rows, cols = a[0], a[1]
        return rows, cols

    def on_resize(self, sig, _) -> None:
        """Fetch new terminal size on resize
        
            Args:
                sig: signal for change (SIGWINCH)
        """
        global p
        if not self.p.closed:
            self.p.setwinsize(*self.get_terminal_size())

    def get_overlay_data(self) -> str:
        """Fetch total usage metrics for the current session

            Returns:
                Formatted string that contains (Tokens/limit | Session reset time | Messages/limit | $ cost/limitCan)
        """
        usage_data = self.log_reader.parse_json_files()
        session_data = SessionData(usage_data=usage_data, plan=self.plan)

        plan_limits = session_data.plan_limits
        total_tokens = session_data.total_tokens()
        session_end = session_data.session_reset_time()
        session_messages = session_data.session_messages()
        total_cost = session_data.total_cost()

        if usage_data:
            return (
                f"Tokens: {total_tokens}/{plan_limits.tokens} | " +
                f"Session reset in: {session_end} | " +
                f"Messages: {session_messages}/{plan_limits.messages} | " +
                f"Cost: {total_cost:.2f}/{plan_limits.cost} $"
            )
        return ""
    
    def locked_os_write(self, fd, data) -> int:
        """Writes Claude outputs through a lock to prevent interleaving between 
            pexpect's output and the overlay. Non-stdout writes pass through without locking.

            Args:
                fd: file descriptor to write to
                data: bytes to write

            Returns:
                Number of bytes written from os.write
        """
        if fd == 1:
            with self.output_lock:
                return self.real_os_write(fd, data)
        return self.real_os_write(fd, data)
        
    def draw_overlay(self):
        """Filter that adds overlay to the bottom of terminal

            Returns:
                Text in bottom line of terminal describing costs of the current input
        """
        ### ANSI CODES:
        ### ref https://stackoverflow.com/questions/11023929/using-the-alternate-screen-in-a-bash-script
        ### ref https://gist.github.com/fnky/458719343aabd01cfb17a3a4f7296797

        while not self.p.closed:
            text = self.get_overlay_data()

            # get terminal dimensions to get to last row
            rows, cols = self.get_terminal_size()

            if text:
                text = text[:cols]

                # cursor manipulation and adding text
                overlay_bytes = (
                    '\x1b[s'                 # save cursor position
                    f'\x1b[{rows-1};1H' +    # move to second-to-last row
                    '\x1b[K' +               # clear that line
                    f'\x1b[{rows};1H' +      # move to last row
                    '\x1b[K' +           # clear the entire line
                    text +               # write the text onto the line
                    '\x1b[u'             # move cursor to saved position
                )
                with self.output_lock:
                    sys.stdout.write(overlay_bytes)
                    sys.stdout.flush()

            time.sleep(1.0) # read logs every other second