import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent
import voice_test


class Content:
    def __init__(self, role, parts): self.role, self.parts = role, parts
class Part:
    def __init__(self, text=None): self.text = text

fake_types = SimpleNamespace(Content=Content, Part=Part,
                             GenerateContentConfig=lambda **kwargs: kwargs,
                             HttpOptions=lambda **kwargs: kwargs,
                             HttpRetryOptions=lambda **kwargs: kwargs)
fake_genai = SimpleNamespace(types=fake_types)


class FallbackTests(unittest.TestCase):
    def test_gemini_timeout_defaults_to_ten_seconds_and_rejects_less(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'key'}, clear=True):
            self.assertEqual(agent.gemini_http_options(fake_types)['timeout'], 10000)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'key', 'GEMINI_LLM_TIMEOUT_SECONDS': '8'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'between 10 and 120'):
                agent.gemini_http_options(fake_types)

    def test_voice_falls_back_only_for_timeout(self):
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kwargs: (_ for _ in ()).throw(TimeoutError())))
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'key'}), \
             patch.dict(sys.modules, {'google': SimpleNamespace(genai=fake_genai), 'google.genai': fake_genai}), \
             patch.object(voice_test, 'gemini_fallback_reason', return_value='gemini_timeout'), \
             patch.object(voice_test, 'openai_fallback', return_value='fallback') as fallback:
            self.assertEqual(voice_test.respond(client, [], 'Hello', 'gemini'), 'fallback')
            fallback.assert_called_once()
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'key'}), \
             patch.dict(sys.modules, {'google': SimpleNamespace(genai=fake_genai), 'google.genai': fake_genai}), \
             patch.object(voice_test, 'gemini_fallback_reason', return_value=None), \
             patch.object(voice_test, 'openai_fallback') as fallback:
            with self.assertRaises(TimeoutError): voice_test.respond(client, [], 'Hello', 'gemini')
            fallback.assert_not_called()

    def test_503_falls_back_for_voice_and_preserves_reason(self):
        class Unavailable(Exception):
            code = 503
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kwargs: (_ for _ in ()).throw(Unavailable())))
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'key'}), \
             patch.dict(sys.modules, {'google': SimpleNamespace(genai=fake_genai), 'google.genai': fake_genai}), \
             patch.object(voice_test, 'openai_fallback', return_value='fallback') as fallback:
            self.assertEqual(voice_test.respond(client, [], 'Hello', 'gemini'), 'fallback')
            self.assertEqual(fallback.call_args.kwargs['reason'], 'gemini_unavailable')

    def test_503_classification_does_not_match_other_errors(self):
        with patch.object(agent, 'is_timeout_error', return_value=False):
            self.assertEqual(agent.gemini_fallback_reason(SimpleNamespace(code=503)), 'gemini_unavailable')
            self.assertIsNone(agent.gemini_fallback_reason(SimpleNamespace(code=400)))

    def test_transcription_503_uses_same_audio_for_openai(self):
        class Unavailable(Exception):
            code = 503
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: (_ for _ in ()).throw(Unavailable())))
        calls = []
        openai = SimpleNamespace(OpenAI=lambda: SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(
            create=lambda **kw: (calls.append(kw) or SimpleNamespace(text='Recognized speech'))))))
        transcription_types = SimpleNamespace(Part=SimpleNamespace(from_bytes=lambda **kw: kw))
        transcription_genai = SimpleNamespace(types=transcription_types)
        with tempfile.NamedTemporaryFile(suffix='.wav') as audio:
            audio.write(b'RIFF test')
            audio.flush()
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'key'}), \
                 patch.dict(sys.modules, {'openai': openai, 'google': SimpleNamespace(genai=transcription_genai), 'google.genai': transcription_genai}):
                self.assertEqual(agent.transcribe(client, audio.name, 'gemini'), 'Recognized speech')
        self.assertEqual(calls[0]['file'][1], b'RIFF test')
        self.assertEqual(calls[0]['model'], 'gpt-transcribe')

    def test_openai_tool_output_includes_camera_image(self):
        call = SimpleNamespace(type='function_call', name='get_image', arguments='{}', call_id='camera')
        class Responses:
            def __init__(self): self.requests = []
            def create(self, **request):
                self.requests.append(list(request['input']))
                if len(self.requests) == 1:
                    return SimpleNamespace(output=[call], output_text='')
                return SimpleNamespace(output=[SimpleNamespace(type='message')], output_text='A chair')
        responses = Responses()
        device = SimpleNamespace(get_image=lambda: {'ok': True, 'mime_type': 'image/png', 'image_bytes': b'png'})
        history = [Content('user', [Part(text='What is in view?')])]
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'key'}), \
             patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=lambda: SimpleNamespace(responses=responses)),
                                      'google': SimpleNamespace(genai=fake_genai), 'google.genai': fake_genai}), \
             patch('agent.time.sleep'):
            self.assertEqual(agent.openai_fallback(history, 'instructions', device), 'A chair')
        self.assertEqual(responses.requests[1][-1]['output'][1]['image_url'], 'data:image/png;base64,cG5n')

    def test_openai_fallback_executes_tools_and_returns_output(self):
        call = SimpleNamespace(type='function_call', name='inspect_screen', arguments='{}', call_id='call1')
        final = SimpleNamespace(type='message')
        class Responses:
            def __init__(self): self.requests = []
            def create(self, **request):
                self.requests.append(list(request['input']))
                return SimpleNamespace(output=[call], output_text='') if len(self.requests) == 1 else SimpleNamespace(output=[final], output_text='Done')
        responses = Responses()
        device = SimpleNamespace(inspect_screen=lambda: {'elements': [{'text': 'Settings'}]})
        history = [Content('user', [Part(text='Open settings')])]
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'key'}), \
             patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=lambda: SimpleNamespace(responses=responses)),
                                      'google': SimpleNamespace(genai=fake_genai), 'google.genai': fake_genai}):
            self.assertEqual(agent.openai_fallback(history, 'instructions', device), 'Done')
        result = responses.requests[1][-1]
        self.assertEqual(result['call_id'], 'call1')
        self.assertEqual(json.loads(result['output'])['elements'][0]['text'], 'Settings')
        self.assertIn('Actions performed during fallback', history[-1].parts[0].text)

if __name__ == '__main__': unittest.main()
