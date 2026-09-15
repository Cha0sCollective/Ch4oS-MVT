"""Harness unit tests only: these do NOT launch or validate Minecraft."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from smoke import CheckFailed, InfrastructureError, Runner, main, parse_score, parse_time, validate_sha


class Parsers(unittest.TestCase):
    def test_score(self):
        self.assertEqual(parse_score('#probe has 1 [mvt]\n', '#probe'), 1)
        self.assertEqual(parse_score('#nonce has -12 [mvt]', '#nonce'), -12)

    def test_reject_command_error(self):
        for text in ('Unknown command', '', '#other has 1 [mvt]', '#probe has 1 [other]'):
            with self.assertRaises(CheckFailed):
                parse_score(text, '#probe')

    def test_time(self):
        self.assertEqual(parse_time('The time is 123'), 123)
        with self.assertRaises(CheckFailed):
            parse_time('Unknown command')

    def test_commit_only(self):
        self.assertEqual(validate_sha('A' * 40), 'a' * 40)
        for text in ('public', 'main; echo bad', '../main', 'a' * 39):
            with self.assertRaises(argparse.ArgumentTypeError):
                validate_sha(text)


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        args = argparse.Namespace(output=str(Path(self.tmp.name) / 'result'),
                                  pack_sha='a' * 40, image='example-image', heap='4G',
                                  container_memory='6g', boot_timeout=1)
        self.runner = Runner(args)

    def test_probe_is_reset_before_command(self):
        calls = []
        self.runner.rcon = lambda command, timeout=30: calls.append(command) or ''
        scores = iter([0, 0])
        self.runner.score = lambda holder: next(scores)
        with self.assertRaises(CheckFailed):
            self.runner.check_command('invalid command')
        self.assertEqual(calls[0], 'scoreboard players set #probe mvt 0')
        self.assertIn('execute store success', calls[1])

    def test_persistence_does_not_initialize_fixture(self):
        self.runner.score = lambda holder: self.runner.nonce
        commands = []
        self.runner.check_command = commands.append
        self.runner.loaded = lambda: None
        self.runner.tick = lambda: None
        self.runner.verify_fixture()
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(command.startswith('execute if ') for command in commands))

    def test_nonce_mismatch_fails(self):
        self.runner.score = lambda holder: self.runner.nonce + 1
        with self.assertRaises(CheckFailed):
            self.runner.verify_fixture()

    def test_failure_skips_downstream_and_records_junit(self):
        with patch.object(self.runner, 'prepare', side_effect=CheckFailed('hash mismatch')):
            with redirect_stdout(StringIO()):
                self.assertEqual(self.runner.run(), 1)
        tests = self.runner.report['tests']
        self.assertEqual(tests[0]['status'], 'failed')
        self.assertTrue(all(test['status'] == 'skipped' for test in tests[1:]))
        root = ET.parse(self.runner.out / 'junit.xml').getroot()
        self.assertEqual(root.get('failures'), '1')
        self.assertFalse(self.runner.report['release_gate_approved'])

    def test_infrastructure_error_is_not_pass(self):
        with patch.object(self.runner, 'prepare', side_effect=InfrastructureError('no docker')):
            with redirect_stdout(StringIO()):
                self.assertEqual(self.runner.run(), 2)
        self.assertEqual(self.runner.report['status'], 'error')

    def test_redaction(self):
        self.assertNotIn(self.runner.password, self.runner.redact('password=' + self.runner.password))

    @patch('smoke.subprocess.run')
    def test_docker_output_uses_utf8_with_replacement(self, mock_run):
        mock_run.return_value = SimpleNamespace(stdout='ok', returncode=0)
        self.assertEqual(self.runner.docker('version'), 'ok')
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs['encoding'], 'utf-8')
        self.assertEqual(kwargs['errors'], 'replace')

    def test_boot_fails_fast_after_done_if_readiness_probe_fails(self):
        self.runner.state = lambda: {'Running': True, 'OOMKilled': False, 'ExitCode': 0}
        self.runner.docker = lambda *args, **kwargs: 'Done (1.0s)!' if args[0] == 'logs' else ''
        calls = []

        def bad_rcon(command, timeout=30):
            calls.append(command)
            raise CheckFailed("Unrecognized time response: 'unexpected'")

        self.runner.rcon = bad_rcon
        with self.assertRaisesRegex(CheckFailed, 'Server reached Done but RCON readiness probe failed'):
            self.runner.boot('fresh')
        self.assertEqual(calls, ['time query gametime'])

    @patch.object(Runner, 'run', return_value=0)
    def test_cli_accepts_host_platform(self, mock_run):
        output = str(Path(self.tmp.name) / 'cli-result')
        argv = ['smoke.py', '--accept-eula', '--pack-sha', 'a' * 40, '--output', output]
        with patch('sys.argv', argv):
            self.assertEqual(main(), 0)
        mock_run.assert_called_once_with()

    def test_existing_output_path_is_clear_cli_error(self):
        argv = ['smoke.py', '--accept-eula', '--pack-sha', 'a' * 40,
                '--output', str(self.runner.out)]
        with patch('sys.argv', argv), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as exc:
                main()
        self.assertEqual(exc.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
