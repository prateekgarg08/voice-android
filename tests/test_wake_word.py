import unittest
import json
import sys
import tempfile
import wave
from types import SimpleNamespace
import agent
from unittest.mock import patch
from agent import listen_for_wake


class WakeWordTests(unittest.TestCase):
    def test_ignores_unrelated_speech_without_cloud_transcription(self):
        with patch('agent.record', return_value='/tmp/fake.wav') as record, \
             patch('agent.local_wake_text', side_effect=['other conversation', 'hello deep open settings']), \
             patch('agent.transcribe', return_value='Hello Deep, open settings') as transcribe, \
             patch('agent.os.unlink'):
            self.assertEqual(listen_for_wake(object()), 'open settings')
            self.assertEqual(record.call_count, 2)
            self.assertEqual(transcribe.call_count, 1)

    def test_wake_word_then_followup(self):
        with patch('agent.record', return_value='/tmp/fake.wav'), \
             patch('agent.local_wake_text', return_value='hello deep'), \
             patch('agent.transcribe', return_value='set chair sound to beep') as transcribe, \
             patch('agent.os.unlink'):
            self.assertEqual(listen_for_wake(object()), 'set chair sound to beep')
            self.assertEqual(transcribe.call_count, 1)

    def test_timeout_after_wake_requires_wake_again(self):
        with patch('agent.record', side_effect=['/tmp/a', TimeoutError('No speech detected.'), '/tmp/b', '/tmp/c']), \
             patch('agent.local_wake_text', side_effect=['hello deep', 'open settings', 'hello deep show camera']), \
             patch('agent.transcribe', return_value='Hello deep show camera') as transcribe, \
             patch('agent.os.unlink'):
            self.assertEqual(listen_for_wake(object()), 'show camera')
            self.assertEqual(transcribe.call_count, 1)

    def test_partial_word_does_not_wake(self):
        with patch('agent.record', return_value='/tmp/fake.wav'), \
             patch('agent.local_wake_text', side_effect=['hello deeply change it', 'hello deep get settings']), \
             patch('agent.transcribe', return_value='hello deep get settings') as transcribe, \
             patch('agent.os.unlink'):
            self.assertEqual(listen_for_wake(object()), 'get settings')
            self.assertEqual(transcribe.call_count, 1)

    def test_repeated_wake_phrase_before_followup(self):
        with patch('agent.record', return_value='/tmp/fake.wav'), \
             patch('agent.local_wake_text', return_value='hello deep'), \
             patch('agent.transcribe', return_value='hello deep, show camera'), \
             patch('agent.os.unlink'):
            self.assertEqual(listen_for_wake(object()), 'show camera')

    def test_local_wake_recognition_uses_local_audio(self):
        class Recognizer:
            def __init__(self, model, rate): self.rate = rate
            def AcceptWaveform(self, chunk): return False
            def FinalResult(self): return json.dumps({'text': 'hello deep'})
        fake_vosk = SimpleNamespace(Model=lambda **kwargs: object(), KaldiRecognizer=Recognizer, SetLogLevel=lambda level: None)
        with tempfile.NamedTemporaryFile(suffix='.wav') as audio:
            with wave.open(audio.name, 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(b'\x00\x00' * 10)
            with patch.dict(sys.modules, {'vosk': fake_vosk}), patch.object(agent, '_WAKE_MODEL', None):
                self.assertEqual(agent.local_wake_text(audio.name), 'hello deep')


if __name__ == '__main__': unittest.main()
