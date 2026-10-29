"""Entry-point configuration tests; live custom-port E2E lives in test_integration."""
import contextlib
import io
import unittest
from unittest.mock import patch

import remote_bet_client
import remote_bet_server


class LaunchTests(unittest.TestCase):
    def test_client_defaults_and_explicit_port_reach_connection_boundary(self):
        for argv, expected in [([], ('127.0.0.1', 5000)),
                               (['127.0.0.1', '--port', '5123'], ('127.0.0.1', 5123))]:
            with patch('remote_bet_client.run_client', return_value=0) as run, \
                 patch('builtins.input', return_value='') as prompt:
                self.assertEqual(remote_bet_client.main(argv), 0)
                run.assert_called_once_with(*expected)
                self.assertEqual(prompt.call_count, 1 if not argv else 0)

    def test_prompt_uses_lan_endpoint_and_retries_invalid_input(self):
        with patch('remote_bet_client.run_client', return_value=0) as run, \
             patch('builtins.input', side_effect=['192.168.1.20', '192.168.1.20:70000',
                                                  ' 192.168.1.20:5123 ']), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(remote_bet_client.main([]), 0)
            run.assert_called_once_with('192.168.1.20', 5123)
            self.assertIn('地址格式', output.getvalue())

    def test_prompt_default_respects_port_option(self):
        with patch('remote_bet_client.run_client', return_value=0) as run, \
             patch('builtins.input', return_value=''):
            self.assertEqual(remote_bet_client.main(['--port', '5123']), 0)
            run.assert_called_once_with('127.0.0.1', 5123)

    def test_prompt_can_quit_without_connection(self):
        for answer in ('quit', EOFError, KeyboardInterrupt):
            with self.subTest(answer=answer), \
                 patch('remote_bet_client.run_client') as run, \
                 patch('builtins.input', side_effect=[answer]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(remote_bet_client.main([]), 0)
                run.assert_not_called()

    def test_endpoint_rejects_invalid_ipv4_and_port(self):
        for text in ('', '192.168.1.20', '999.1.1.1:5000', '1.2.3.4:0',
                     '1.2.3.4:65536', '1.2.3.4:abc', '1.2.3.4:+5000',
                     '1.2.3.4:５０００', 'http://1.2.3.4:5000', '[::1]:5000'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                remote_bet_client.parse_endpoint(text)
        self.assertEqual(remote_bet_client.parse_endpoint('192.168.1.20:65535'),
                         ('192.168.1.20', 65535))

    def test_server_defaults_and_port_reach_binding_boundary(self):
        for argv, expected in [([], ('127.0.0.1', 5000)),
                               (['--host', '127.0.0.1', '--port', '5123'], ('127.0.0.1', 5123))]:
            with patch('remote_bet_server.create_listener', side_effect=OSError('occupied')) as bind, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(remote_bet_server.main(argv), 1)
                bind.assert_called_once_with(*expected)

    def test_server_main_interrupt_is_normal_exit(self):
        with patch('remote_bet_server.run_server', side_effect=KeyboardInterrupt), \
             patch('remote_bet_server.create_listener'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(remote_bet_server.main([]), 0)
