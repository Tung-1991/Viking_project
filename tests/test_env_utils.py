# -*- coding: utf-8 -*-

import os
import tempfile
import unittest

from core import env_utils


class EnvUtilsTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".env")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_update_existing_key_preserves_others_and_comments(self):
        self._write("# header\nDNSE_API_KEY=old\nOTHER=keep\n")
        env_utils.update_env({"DNSE_API_KEY": "new"}, path=self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# header", content)
        self.assertIn("OTHER=keep", content)
        values = env_utils.load_env(self.path)
        self.assertEqual(values["DNSE_API_KEY"], "new")
        self.assertEqual(values["OTHER"], "keep")

    def test_append_new_key(self):
        self._write("EXISTING=1\n")
        env_utils.update_env({"DNSE_WS_ENABLED": "true"}, path=self.path)
        values = env_utils.load_env(self.path)
        self.assertEqual(values["EXISTING"], "1")
        self.assertEqual(values["DNSE_WS_ENABLED"], "true")

    def test_update_collapses_duplicate_keys_including_stale_blank(self):
        self._write("DNSE_API_KEY=old\nDNSE_API_KEY=\nOTHER=keep\n")
        env_utils.update_env({"DNSE_API_KEY": "new"}, path=self.path)
        values = env_utils.load_env(self.path)
        self.assertEqual(values["DNSE_API_KEY"], "new")
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read().count("DNSE_API_KEY="), 1)

    def test_create_file_when_missing(self):
        os.remove(self.path)
        env_utils.update_env({"DNSE_STOCK_ACCOUNT_NO": "12345"}, path=self.path)
        self.assertEqual(env_utils.load_env(self.path)["DNSE_STOCK_ACCOUNT_NO"], "12345")

    def test_load_strips_quotes(self):
        self._write('KEY="quoted value"\n')
        self.assertEqual(env_utils.load_env(self.path)["KEY"], "quoted value")

    def test_secret_is_set_in_process_without_writing_env_file(self):
        self._write("KEEP=1\n")
        old_value = os.environ.get("RAT_TEST_SECRET")
        try:
            result = env_utils.set_user_environment_secret(
                "RAT_TEST_SECRET", "very-secret", persist=False
            )
            self.assertTrue(result["ok"])
            self.assertNotIn("very-secret", str(result))
            self.assertEqual(os.environ["RAT_TEST_SECRET"], "very-secret")
            with open(self.path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "KEEP=1\n")
        finally:
            if old_value is None:
                os.environ.pop("RAT_TEST_SECRET", None)
            else:
                os.environ["RAT_TEST_SECRET"] = old_value

    def test_secret_rejects_invalid_environment_name(self):
        with self.assertRaises(ValueError):
            env_utils.set_user_environment_secret("BAD NAME", "secret", persist=False)


if __name__ == "__main__":
    unittest.main()
