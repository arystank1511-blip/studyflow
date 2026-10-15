"""Sentry reports must never carry students' data."""
import os
import unittest
from unittest.mock import patch

import monitoring


class MonitoringTests(unittest.TestCase):
    def test_off_without_dsn(self):
        with patch.dict(os.environ, {"SENTRY_DSN": ""}):
            self.assertFalse(monitoring.init())

    def test_scrub_keeps_only_what_fixes_a_bug(self):
        event = {
            "exception": {"values": [{"type": "KeyError"}]},
            "request": {"method": "POST", "url": "https://x.app/tasks/5/edit?q=secret", "data": {"title": "Diary"},
                        "cookies": {"sf_access": "jwt"}, "headers": {"Authorization": "Bearer jwt"}, "query_string": "q=secret"},
            "user": {"email": "student@example.com", "ip_address": "1.2.3.4"},
            "breadcrumbs": {"values": [{"message": "SELECT title"}]},
            "extra": {"task": "Diary"},
            "contexts": {"runtime": {"name": "CPython"}, "flask": {"session": "x"}},
        }
        clean = monitoring.scrub(event)
        self.assertEqual(clean["request"], {"method": "POST", "url": "https://x.app/tasks/5/edit"})
        self.assertNotIn("user", clean)
        self.assertNotIn("breadcrumbs", clean)
        self.assertEqual(clean["extra"], {})
        self.assertEqual(clean["contexts"], {"runtime": {"name": "CPython"}})
        self.assertEqual(clean["exception"], {"values": [{"type": "KeyError"}]})
        self.assertNotIn("jwt", repr(clean))
        self.assertNotIn("secret", repr(clean))

    def test_init_with_dsn_uses_private_settings(self):
        with patch.dict(os.environ, {"SENTRY_DSN": "https://key@o0.ingest.sentry.io/0"}), patch("sentry_sdk.init") as init:
            self.assertTrue(monitoring.init())
        options = init.call_args.kwargs
        self.assertFalse(options["send_default_pii"])
        self.assertFalse(options["include_local_variables"])
        self.assertEqual(options["max_request_body_size"], "never")
        self.assertIs(options["before_send"], monitoring.scrub)


if __name__ == "__main__":
    unittest.main()
