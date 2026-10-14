"""LUFS target validation and user-facing scope regression tests."""
from __future__ import annotations

import inspect
import math
import unittest
from types import SimpleNamespace

from video_maker.api.control import apply_settings
from video_maker.api.openapi import openapi_spec
from video_maker.config.settings import Settings
from video_maker.gui.app import App


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value


class TestLufsValidation(unittest.TestCase):
    def test_supported_lufs_targets_are_accepted(self):
        for target in (-36, -14, -5, -24.5):
            with self.subTest(target=target):
                settings = Settings(target_lufs=target)
                self.assertFalse(any("Целевой LUFS" in error for error in settings.validate()))

    def test_nan_infinity_non_numeric_and_out_of_range_are_rejected(self):
        for target in (float("nan"), float("inf"), float("-inf"), "not-a-number", -40, 0, 12):
            with self.subTest(target=target):
                settings = Settings(target_lufs=target)
                errors = settings.validate()
                self.assertTrue(any("Целевой LUFS" in error for error in errors))

    def test_api_accepts_numeric_value_and_rejects_invalid_without_mutating(self):
        settings = Settings()
        app = SimpleNamespace(settings=settings, target_lufs_var=FakeVar("-14.0"))
        applied, unknown = apply_settings(app, {"target_lufs": -21.5})
        self.assertIn("target_lufs", applied)
        self.assertNotIn("target_lufs", unknown)
        self.assertEqual(settings.target_lufs, -21.5)
        self.assertEqual(app.target_lufs_var.value, "-21.5")

        for value in ("nan", "inf", "bad", -40, -2):
            with self.subTest(value=value):
                before = settings.target_lufs
                applied, unknown = apply_settings(app, {"target_lufs": value})
                self.assertNotIn("target_lufs", applied)
                self.assertIn("target_lufs", unknown)
                self.assertEqual(settings.target_lufs, before)

    def test_openapi_documents_scope_and_valid_range(self):
        spec = openapi_spec()
        properties = spec["paths"]["/api/settings"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
        field = properties["target_lufs"]
        self.assertEqual(field["minimum"], -36)
        self.assertEqual(field["maximum"], -5)
        self.assertIn("только для финального микса с BGM", field["description"])

    def test_gui_label_explains_bgm_only_and_voice_only_fast_path(self):
        source = inspect.getsource(App._build_main_tab)
        self.assertIn("Целевой LUFS для микса с BGM", source)
        self.assertIn("Voice-only выход не проходит отдельную нормализацию", source)


if __name__ == "__main__":
    unittest.main()
