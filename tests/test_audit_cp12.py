"""Deep-audit CP12 regressions: command line and process lifecycle."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from unittest.mock import patch

import tkinter as tk

from video_maker.main import _parse_args, _sigint_handler, _sigterm_handler, main


class TestDeepAuditCli(unittest.TestCase):
    def test_help_is_available(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exc:
            _parse_args(["--help"])
        self.assertEqual(exc.exception.code, 0)
        self.assertIn("--api-port", out.getvalue())

    def test_valid_flags_are_parsed(self):
        self.assertEqual(_parse_args(["--headless", "--api-port", "8765", "--no-api"]),
                         (True, 8765, True))

    def test_out_of_range_port_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            _parse_args(["--api-port", "70000"])
        self.assertEqual(exc.exception.code, 2)

    def test_unknown_flag_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            _parse_args(["--not-a-real-flag"])
        self.assertEqual(exc.exception.code, 2)

    def test_ctrl_c_is_not_reported_as_success(self):
        with self.assertRaises(KeyboardInterrupt):
            _sigint_handler(2, None)

    def test_sigterm_has_termination_status(self):
        with self.assertRaises(SystemExit) as exc:
            _sigterm_handler(15, None)
        self.assertEqual(exc.exception.code, 143)

    def test_gui_startup_exception_has_nonzero_status(self):
        with (
            patch.object(sys, "argv", ["VideoMaker"]),
            patch("video_maker.main.tk.Tk", side_effect=tk.TclError("synthetic display failure")),
            self.assertRaises(SystemExit) as exc,
        ):
            main()
        self.assertEqual(exc.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
