import json
import tempfile
import unittest
from pathlib import Path
from tool_test import SimulatedAppTools


class SimulatedToolsTests(unittest.TestCase):
    def test_changes_are_temporary_and_camera_is_simulated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            initial = {'objects': [{'chair': {'sound': 'beep'}}]}
            path.write_text(json.dumps(initial), encoding='utf-8')
            tools = SimulatedAppTools(path)
            try:
                tools.update_configuration('/objects/0/chair/sound', '"chime"')
                self.assertEqual(tools.get_configuration()['configuration']['objects'][0]['chair']['sound'], 'chime')
                self.assertEqual(json.loads(path.read_text()), initial)
                result = tools.get_image()
                self.assertTrue(result['simulated'])
                self.assertNotIn('image_bytes', result)
            finally:
                tools.temp_dir.cleanup()

if __name__ == '__main__': unittest.main()
