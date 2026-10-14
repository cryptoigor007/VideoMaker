"""CLI parsing and process exit status regressions."""
from __future__ import annotations

import contextlib
import io
import inspect
import sys
import unittest
from unittest.mock import patch

import tkinter as tk

from video_maker.main import _parse_args, main
from video_maker.gui.app import App


class TestCliArguments(unittest.TestCase):
    def test_help_exits_before_starting_gui(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as ctx:
            _parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("--headless", output.getvalue())
        self.assertIn("--api-port", output.getvalue())
        self.assertIn("--no-api", output.getvalue())

    def test_valid_options_are_parsed(self):
        self.assertEqual(_parse_args(["--headless", "--api-port", "18765", "--no-api"]),
                         (True, 18765, True))

    def test_invalid_port_fails_fast(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            _parse_args(["--api-port", "99999"])
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_option_fails_fast(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            _parse_args(["--typo-option"])
        self.assertEqual(ctx.exception.code, 2)

    def test_pipeline_worker_does_not_read_tk_variables_directly(self):
        source = inspect.getsource(App._run_pipeline)
        forbidden = (
            "self._var_get(",
            "self._packaging_opts_from_settings(",
            "self.caption_style_var.get(",
            "self.hook_style_var.get(",
            "self.shorts_desc_max_len_var.get(",
            "self._settings_prompt_extra_box.get(",
            "self._settings_prompt_box.get(",
        )
        for expression in forbidden:
            self.assertNotIn(expression, source, f"pipeline worker still reads Tk state: {expression}")

    def test_sigint_is_interruption_not_success(self):
        from video_maker.main import _sigint_handler
        with self.assertRaises(KeyboardInterrupt):
            _sigint_handler(2, None)

    def test_gui_startup_failure_has_nonzero_exit_code(self):
        with (
            patch.object(sys, "argv", ["VideoMaker"]),
            patch("video_maker.main.tk.Tk", side_effect=tk.TclError("synthetic display failure")),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
