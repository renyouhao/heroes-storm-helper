import unittest

from hots_chat.desktop_protocol import COMMANDS, ProtocolError, validate_command, validate_request


class DesktopProtocolTests(unittest.TestCase):
    def test_all_command_names_are_explicit_and_request_ids_are_bounded(self):
        self.assertIn('translate', COMMANDS)
        self.assertEqual(validate_request({'request_id': 'r1', 'command': 'snapshot'}),
                         ('r1', 'snapshot', {}))
        for request in ([], {}, {'request_id': True, 'command': 'snapshot'},
                        {'request_id': 'x' * 129, 'command': 'snapshot'},
                        {'request_id': 1, 'command': 'snapshot', 'extra': 'bad'}):
            with self.subTest(request=request), self.assertRaises(ProtocolError):
                validate_request(request)

    def test_invalid_values_and_unrecognized_fields_are_rejected(self):
        cases = [
            ('__dict__', {}), ('configure_account', {}),
            ('snapshot', {'credentials': True}), ('draft', []),
            ('draft', {'text': 123}), ('draft', {'text': 'x' * 1001}),
            ('translate', {'text': ' ', 'target': 'en'}),
            ('translate', {'text': '你好', 'target': 'zh'}),
            ('translate', {'text': '你好'}), ('set_auto', {'enabled': 1}),
            ('set_language', {'target': ['en']}),
            ('set_channel', {'value': '非法频道'}), ('set_channel', {'value': []}),
            ('repeat_config', {'action': 'ALT+F4', 'interval': 20}),
            ('repeat_config', {'action': 'S', 'interval': True}),
            ('repeat_config', {'action': 'YS', 'interval': 19}),
            ('repeat_config', {'action': 'YS', 'interval': 5001}),
            ('helper_retry', {'source': '__class__'}),
            ('phrase_delete', {'id': ''}), ('retry_message', {'id': 1}),
        ]
        for command, payload in cases:
            with self.subTest(command=command, payload=payload), self.assertRaises(ProtocolError):
                validate_command(command, payload)

    def test_valid_payload_is_copied_and_optional_phrase_edit_id_supported(self):
        payload = {'text': '', 'target': 'en'}
        self.assertEqual(validate_command('phrase_edit', {}), {})
        self.assertEqual(validate_command('phrase_edit', {'id': None}), {'id': None})
        payload = {'text': '你好', 'target': 'en'}
        result = validate_command('translate', payload)
        self.assertEqual(result, payload)
        self.assertIsNot(result, payload)
