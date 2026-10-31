import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import _record_until_silence


class VadTests(unittest.TestCase):
    def test_end_of_speech_returns_complete_audio_and_closes_stream(self):
        events = SimpleNamespace(START_OF_SPEECH='start', END_OF_SPEECH='end')
        audio = SimpleNamespace(data=memoryview(b'\x01\x00\x02\x00'))
        class Stream:
            closed = False
            def __init__(self):
                self.events = iter([SimpleNamespace(type='start'),
                                    SimpleNamespace(type='end', frames=[audio], speech_duration=1.0, silence_duration=.8)])
            async def __anext__(self): return next(self.events)
            async def aclose(self): self.closed = True
            def push_frame(self, frame): pass
        stream = Stream()
        class Input:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
        modules = {
            'sounddevice': SimpleNamespace(RawInputStream=Input),
            'livekit': SimpleNamespace(rtc=SimpleNamespace(AudioFrame=lambda **kw: kw)),
            'livekit.agents': SimpleNamespace(vad=SimpleNamespace(VADEventType=events)),
            'livekit.plugins': SimpleNamespace(silero=SimpleNamespace(VAD=SimpleNamespace(load=lambda **kw: SimpleNamespace(stream=lambda: stream)))),
        }
        with patch.dict(sys.modules, modules):
            self.assertEqual(asyncio.run(_record_until_silence(.8, 15, 60)), b'\x01\x00\x02\x00')
        self.assertTrue(stream.closed)


if __name__ == '__main__': unittest.main()
