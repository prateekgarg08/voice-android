import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
from configuration_tools import ConfigurationTools
from agent import history_for_fallback


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'settings.json'
        self.path.write_text(json.dumps({'objects': [{'chair': {'sound': 'beep', 'enabled': True}}]}))
        self.tools = ConfigurationTools(self.path)

    def test_update_existing_setting_and_persist(self):
        self.assertEqual(self.tools.get_configuration()['configuration']['objects'][0]['chair']['sound'], 'beep')
        result = self.tools.update_configuration('/objects/0/chair/sound', '"chime"')
        self.assertEqual(result['previous_value'], 'beep')
        self.assertEqual(json.loads(self.path.read_text())['objects'][0]['chair']['sound'], 'chime')

    def test_invalid_updates_leave_file_unchanged(self):
        before = self.path.read_text()
        for path, value in [('/objects/0/chair/missing', '"x"'), ('/objects/0/chair/sound', 'true'), ('/objects/0', '{}')]:
            with self.assertRaises(ValueError): self.tools.update_configuration(path, value)
        self.assertEqual(self.path.read_text(), before)

    def test_camera_returns_jpeg_and_releases(self):
        camera = SimpleNamespace(isOpened=lambda: True, read=lambda: (True, 'frame'), release=lambda: calls.append('released'))
        calls = []
        cv2 = SimpleNamespace(VideoCapture=lambda i: camera, imencode=lambda ext, frame: (True, SimpleNamespace(tobytes=lambda: b'jpeg')))
        with patch.dict(sys.modules, {'cv2': cv2}):
            result = self.tools.get_image()
        self.assertEqual(result['image_bytes'], b'jpeg')
        self.assertEqual(calls, ['released'])

    def test_fallback_history_includes_camera_image(self):
        blob = SimpleNamespace(data=b'jpeg', mime_type='image/jpeg')
        result = SimpleNamespace(name='get_image', response={'ok': True}, parts=[SimpleNamespace(inline_data=blob)])
        history = [SimpleNamespace(role='user', parts=[SimpleNamespace(function_response=result)])]
        messages = history_for_fallback(history)
        self.assertTrue(messages[0]['content'][1]['image_url'].startswith('data:image/jpeg;base64,'))

if __name__ == '__main__': unittest.main()

class GeminiImageTests(unittest.TestCase):
    def test_camera_frame_is_attached_to_tool_response(self):
        try:
            from google.genai import types
        except ImportError:
            self.skipTest('Google GenAI SDK is not installed')
        from agent import run_turn
        from configuration_tools import TOOLS, SYSTEM
        call = types.Part(function_call=types.FunctionCall(name='get_image', args={}))
        output = types.Content(role='model', parts=[call])
        done = types.Content(role='model', parts=[types.Part(text='A chair')])
        class Models:
            def __init__(self): self.requests = []
            def generate_content(self, **kwargs):
                self.requests.append(list(kwargs['contents']))
                content = output if len(self.requests) == 1 else done
                return SimpleNamespace(candidates=[SimpleNamespace(content=content)], text='A chair')
        models = Models()
        device = SimpleNamespace(get_image=lambda: {'ok': True, 'mime_type': 'image/jpeg', 'image_bytes': b'jpeg'})
        with patch.dict('os.environ', {'OPENAI_API_KEY': ''}):
            with patch('agent.time.sleep'):
                result = run_turn(SimpleNamespace(models=models), device, [], 'gemini', 'What is visible?', tool_definitions=TOOLS, instructions=SYSTEM)
        self.assertEqual(result, 'A chair')
        returned = models.requests[1][-1].parts[0].function_response
        self.assertEqual(returned.parts[0].inline_data.data, b'jpeg')
