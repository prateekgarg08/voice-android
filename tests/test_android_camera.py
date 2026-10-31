import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import Android, TOOLS, openai_fallback

PNG = b'\x89PNG\r\n\x1a\nimage'


class FakeCameraAndroid(Android):
    def __init__(self):
        super().__init__('serial123')
        self.commands = []
        self.elements = [{'index': 0}]
        self.snapshot_xml = 'old'
        self.launch_output = 'Status: ok'
    def adb(self, *args):
        self.commands.append(args)
        return self.launch_output if args[:3] == ('shell', 'am', 'start') else ''


class AndroidCameraTests(unittest.TestCase):
    def test_camera_launch_capture_and_return(self):
        device = FakeCameraAndroid()
        with patch('agent.time.sleep'), patch('agent.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=PNG, stderr=b'')) as command:
            result = device.get_image()
        self.assertIn('get_image', [item['name'] for item in TOOLS])
        self.assertEqual(device.commands[0], ('shell', 'am', 'start', '-W', '-a', 'android.media.action.STILL_IMAGE_CAMERA'))
        self.assertEqual(device.commands[-1], ('shell', 'input', 'keyevent', 'KEYCODE_BACK'))
        self.assertEqual(command.call_args.args[0], ['adb', '-s', 'serial123', 'exec-out', 'screencap', '-p'])
        self.assertEqual(result['image_bytes'], PNG)
        self.assertEqual(result['mime_type'], 'image/png')
        self.assertEqual(device.elements, [])
        self.assertIsNone(device.snapshot_xml)

    def test_capture_failure_returns_from_camera(self):
        device = FakeCameraAndroid()
        with patch('agent.time.sleep'), patch('agent.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout=b'', stderr=b'failed')):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                device.get_image()
        self.assertEqual(device.commands[-1], ('shell', 'input', 'keyevent', 'KEYCODE_BACK'))

    def test_launch_failure_does_not_press_back(self):
        device = FakeCameraAndroid()
        device.launch_output = 'Error: unable to resolve Intent'
        with self.assertRaisesRegex(RuntimeError, 'Could not launch'):
            device.get_image()
        self.assertEqual(len(device.commands), 1)


if __name__ == '__main__': unittest.main()
