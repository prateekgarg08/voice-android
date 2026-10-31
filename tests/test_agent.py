import json
import unittest
import sys
import os
import tempfile
from unittest.mock import patch
from types import SimpleNamespace
from agent import Android, run_turn, api_call, configure_logging, TURN_ID, LOGGER, log_transcription_audio, transcription_text, load_environment

XML = '''<?xml version="1.0"?><hierarchy><node text="Notifications" content-desc="" resource-id="app:id/notifications" class="android.widget.Switch" bounds="[0,10][100,50]" enabled="true" clickable="true" checkable="true" checked="true" scrollable="false"/></hierarchy>'''

class FakeAndroid(Android):
    def __init__(self):
        super().__init__()
        self.xml = XML
        self.commands = []
    def hierarchy(self):
        return self.xml
    def adb(self, *args):
        self.commands.append(args)
        return 'Physical size: 1080x2400' if args == ('shell', 'wm', 'size') else ''

class AndroidTests(unittest.TestCase):
    def test_inspect_and_tap(self):
        device = FakeAndroid()
        screen = device.inspect_screen()
        self.assertTrue(screen['elements'][0]['checked'])
        device.tap(0)
        self.assertEqual(device.commands[-1], ('shell', 'input', 'tap', '50', '30'))
        with self.assertRaises(ValueError):
            device.tap(0)
    def test_reject_stale_snapshot(self):
        device = FakeAndroid()
        device.inspect_screen()
        device.xml = XML.replace('checked="true"', 'checked="false"')
        with self.assertRaisesRegex(ValueError, 'changed'):
            device.tap(0)
        self.assertEqual(device.commands, [])
    def test_reject_shell_metacharacters(self):
        device = FakeAndroid()
        with self.assertRaises(ValueError):
            device.type_text('hello; reboot')
        self.assertEqual(device.commands, [])
    def test_tool_loop_returns_outputs_to_model(self):
        class Part:
            def __init__(self, text=None, function_call=None):
                self.text, self.function_call = text, function_call
            @staticmethod
            def from_function_response(**kwargs):
                return SimpleNamespace(**kwargs)
        class Content:
            def __init__(self, role, parts): self.role, self.parts = role, parts
        fake_types = SimpleNamespace(Part=Part, Content=Content,
                                     GenerateContentConfig=lambda **kw: kw,
                                     Tool=lambda **kw: kw,
                                     AutomaticFunctionCallingConfig=lambda **kw: kw)
        fake_genai = SimpleNamespace(types=fake_types)
        call = SimpleNamespace(name='inspect_screen', args={}, id='c1')
        model_content = Content('model', [Part(function_call=call)])
        final_content = Content('model', [Part(text='Done')])
        class Models:
            def __init__(self): self.requests = []
            def generate_content(self, **kwargs):
                self.requests.append(list(kwargs['contents']))
                content = model_content if len(self.requests) == 1 else final_content
                return SimpleNamespace(candidates=[SimpleNamespace(content=content)], text='Done')
        models = Models()
        history = []
        with patch.dict(sys.modules, {'google': SimpleNamespace(genai=fake_genai), 'google.genai': fake_genai}):
            self.assertEqual(run_turn(SimpleNamespace(models=models), FakeAndroid(), history, 'model', 'Inspect'), 'Done')
        self.assertIs(models.requests[1][-2], model_content)
        output = models.requests[1][-1].parts[0]
        self.assertEqual(output.id, 'c1')
        self.assertEqual(output.response['elements'][0]['text'], 'Notifications')

class EnvironmentTests(unittest.TestCase):
    def test_loads_project_env_without_overriding_shell(self):
        calls = []
        dotenv = SimpleNamespace(load_dotenv=lambda **kwargs: calls.append(kwargs))
        with patch.dict(sys.modules, {'dotenv': dotenv}):
            load_environment()
        self.assertEqual(calls[0]['dotenv_path'].name, '.env')
        self.assertEqual(calls[0]['dotenv_path'].parent, __import__('pathlib').Path(__file__).resolve().parent.parent)
        self.assertFalse(calls[0]['override'])


    def test_openai_key_alias_and_standard_key_precedence(self):
        dotenv = SimpleNamespace(load_dotenv=lambda **kwargs: None)
        with patch.dict(sys.modules, {'dotenv': dotenv}), patch.dict(os.environ, {'OPEN_AI_API_KEY': 'alias'}, clear=True):
            load_environment()
            self.assertEqual(os.environ['OPENAI_API_KEY'], 'alias')
        with patch.dict(sys.modules, {'dotenv': dotenv}), patch.dict(os.environ, {'OPEN_AI_API_KEY': 'alias', 'OPENAI_API_KEY': 'standard'}, clear=True):
            load_environment()
            self.assertEqual(os.environ['OPENAI_API_KEY'], 'standard')


class TranscriptionTests(unittest.TestCase):
    def test_text_with_audio_metadata_is_not_duplicated(self):
        parts = [SimpleNamespace(text="Turn on "), SimpleNamespace(text="notifications"),
                 SimpleNamespace(audio_transcription=SimpleNamespace(text="Turn on notifications"))]
        response = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))])
        self.assertEqual(transcription_text(response), "Turn on notifications")

    def test_audio_transcription_fallback_and_empty(self):
        parts = [SimpleNamespace(audio_transcription=SimpleNamespace(text="Hello")),
                 SimpleNamespace(audio_transcription=SimpleNamespace(text="world"))]
        response = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))])
        self.assertEqual(transcription_text(response), "Hello world")
        self.assertEqual(transcription_text(SimpleNamespace(candidates=[])), "")


class LoggingTests(unittest.TestCase):
    def test_requests_responses_errors_and_redaction(self):
        class Response:
            def model_dump(self, **kwargs):
                return {"text": "finished", "usage_metadata": {"total_token_count": 12},
                        "inline_data": {"data": b"audio"}}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GEMINI_API_KEY": "secret-key"}):
            path = os.path.join(directory, "agent.jsonl")
            configure_logging(path, "INFO")
            token = TURN_ID.set("turn-1")
            try:
                api_call("generate_content", {"prompt": "hello", "api_key": "secret-key"}, Response)
                with self.assertRaises(ValueError):
                    api_call("generate_content", {}, lambda: (_ for _ in ()).throw(ValueError("bad secret-key")))
                for handler in LOGGER.handlers: handler.flush()
                with open(path, encoding="utf-8") as f:
                    records = [json.loads(line) for line in f]
                self.assertEqual([r['payload']['event'] for r in records],
                                 ['api_request', 'api_response', 'api_request', 'api_error'])
                first, response, failed, error = [r['payload'] for r in records]
                self.assertEqual(first['turn_id'], 'turn-1')
                self.assertEqual(first['request_id'], response['request_id'])
                self.assertEqual(failed['request_id'], error['request_id'])
                self.assertEqual(response['response']['usage_metadata']['total_token_count'], 12)
                self.assertEqual(response['response']['inline_data']['data'], '[omitted]')
                self.assertIn('duration_ms', error)
                self.assertNotIn('secret-key', json.dumps(records))
            finally:
                TURN_ID.reset(token)
                configure_logging(path, "OFF")

    def test_transcription_audio_saved_and_correlated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "agent.jsonl")
            configure_logging(path, "INFO")
            token = TURN_ID.set("turn-audio")
            try:
                log_transcription_audio(b"RIFF sample")
                for handler in LOGGER.handlers: handler.flush()
                with open(path, encoding="utf-8") as f:
                    event = json.loads(f.readline())["payload"]
                self.assertEqual(event["event"], "transcription_audio")
                self.assertEqual(event["turn_id"], "turn-audio")
                with open(event["path"], "rb") as f:
                    self.assertEqual(f.read(), b"RIFF sample")
                self.assertEqual(os.stat(event["path"]).st_mode & 0o777, 0o600)
            finally:
                TURN_ID.reset(token)
                configure_logging(path, "OFF")

    def test_warning_level_keeps_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "agent.jsonl")
            configure_logging(path, "WARNING")
            try:
                with self.assertRaises(RuntimeError):
                    api_call("test", {}, lambda: (_ for _ in ()).throw(RuntimeError("failure")))
                for handler in LOGGER.handlers: handler.flush()
                with open(path, encoding="utf-8") as f:
                    records = [json.loads(line) for line in f]
                self.assertEqual([r['payload']['event'] for r in records], ['api_error'])
            finally:
                configure_logging(path, "OFF")

if __name__ == '__main__': unittest.main()
