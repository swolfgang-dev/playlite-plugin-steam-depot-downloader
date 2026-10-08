import importlib.util
from pathlib import Path
import tempfile
import unittest
import test_worker
from downloader.steam_queue_runner import provider_limit_message

spec=importlib.util.spec_from_file_location('provider_limits',Path(__file__).resolve().parents[1]/'tools/steam/provider_limits.py')
limits=importlib.util.module_from_spec(spec);spec.loader.exec_module(limits)

class ProviderLimitsTests(unittest.TestCase):
    def test_numeric_limits_and_cooldown_without_sensitive_fields(self):
        row=limits.parse_limits('Luie',429,'Retry-After: 45\nX-RateLimit-Remaining: 0\nSet-Cookie: SECRET',
                                '{"daily_usage":10,"daily_limit":10,"token":"SECRET"}')
        message=provider_limit_message(row)
        self.assertIn('10/10',message);self.assertIn('45 seconds',message)
        self.assertIn('0 remaining',message)
        self.assertNotIn('SECRET',str(row))

    def test_throttling_does_not_invent_daily_limit_or_reset(self):
        row=limits.parse_limits('Luie',429,'','{"error":"too many requests"}')
        self.assertIn('remaining quota not reported',provider_limit_message(row))
        self.assertIn('retry time not reported',provider_limit_message(row))
        self.assertNotIn('daily_limit',row)

    def test_capture_patch_is_idempotent_and_retains_only_safe_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=Path(directory);(backend/'scripts').mkdir()
            script=backend/'scripts/smart_download.sh';script.write_text('''#!/bin/bash
    elif [[ "${clean,,}" =~ ^\\<\\ content-length:[[:space:]]*([0-9]+)$ ]]; then
    limit_reason="$(source_limit_reason "${http:-0}" "${C_ZIP[i]}")"
''')
            limits.install_capture(backend);first=script.read_text()
            self.assertIn('python3 '+str(Path(limits.__file__).resolve()),first)
            self.assertIn('retry-after|x-ratelimit-',first)
            limits.install_capture(backend);self.assertEqual(script.read_text(),first)
