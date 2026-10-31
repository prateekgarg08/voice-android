import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from firebase_admin import auth
import api


class AuthenticatedApiTests(unittest.TestCase):
    def setUp(self):
        def replace(target, **kwargs):
            item = patch(target, **kwargs)
            value = item.start()
            self.addCleanup(item.stop)
            return value
        replace('api.load_environment')
        replace('api.configure_logging')
        replace('api.firebase_app', return_value=object())
        self.verifier = replace('api.auth.verify_id_token', return_value={'uid': 'user-1'})
        self.execute = replace('api.execute_turn', return_value='Done')
        self.transcribe = replace('api.transcribe_audio', return_value='Open settings')
        self.speech = replace('api.generate_speech', return_value=b'RIFF-sound')
        self.env = patch.dict('os.environ', {'ALLOWED_FIREBASE_UIDS': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(api.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.auth_header = {'Authorization': 'Bearer firebase-id-token'}

    def test_missing_or_wrong_scheme_never_executes_tools(self):
        for headers in ({}, {'Authorization': 'Basic abc'}):
            response = self.client.post('/v1/agent/turn', headers=headers, json={'command': 'Open settings'})
            self.assertEqual(response.status_code, 401)
        self.execute.assert_not_called()
        self.verifier.assert_not_called()

    def test_invalid_and_revoked_token_never_executes_tools(self):
        for exc in (auth.InvalidIdTokenError('bad'), auth.RevokedIdTokenError('revoked')):
            with patch('api.auth.verify_id_token', side_effect=exc):
                response = self.client.post('/v1/agent/turn', headers=self.auth_header, json={'command': 'Open settings'})
                self.assertEqual(response.status_code, 401)
        self.execute.assert_not_called()

    def test_valid_token_runs_single_turn_and_checks_revocation(self):
        response = self.client.post('/v1/agent/turn', headers=self.auth_header, json={'command': ' Open settings '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'reply': 'Done'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.execute.assert_called_once_with('Open settings')
        self.verifier.assert_called_once()
        self.assertTrue(self.verifier.mock_calls[0].kwargs['check_revoked'])

    def test_disallowed_user_and_unavailable_auth(self):
        with patch.dict('os.environ', {'ALLOWED_FIREBASE_UIDS': 'other-user'}):
            self.assertEqual(self.client.post('/v1/agent/turn', headers=self.auth_header, json={'command': 'test'}).status_code, 403)
        with patch('api.firebase_app', side_effect=RuntimeError('missing credentials')), self.assertLogs('voice_agent_api', level='ERROR'):
            self.assertEqual(self.client.post('/v1/agent/turn', headers=self.auth_header, json={'command': 'test'}).status_code, 503)
        self.execute.assert_not_called()

    def test_verification_outage_fails_closed(self):
        with patch('api.auth.verify_id_token', side_effect=RuntimeError('verification unavailable')), \
             self.assertLogs('voice_agent_api', level='ERROR'):
            response = self.client.post('/v1/agent/turn', headers=self.auth_header, json={'command': 'test'})
        self.assertEqual(response.status_code, 503)
        self.execute.assert_not_called()

    def test_audio_and_speech_require_authentication(self):
        audio = b'RIFF\x00\x00\x00\x00WAVEhello'
        self.assertEqual(self.client.post('/v1/agent/audio', content=audio, headers={'Content-Type': 'audio/wav'}).status_code, 401)
        self.assertEqual(self.client.post('/v1/agent/speech', json={'text': 'hello'}).status_code, 401)
        self.transcribe.assert_not_called()
        self.speech.assert_not_called()
        headers = {**self.auth_header, 'Content-Type': 'audio/wav'}
        response = self.client.post('/v1/agent/audio', content=audio, headers=headers)
        self.assertEqual(response.json(), {'transcript': 'Open settings', 'reply': 'Done'})
        self.transcribe.assert_called_once_with(audio)
        response = self.client.post('/v1/agent/speech', headers=self.auth_header, json={'text': 'Hello'})
        self.assertEqual(response.content, b'RIFF-sound')
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_invalid_or_oversized_content_is_rejected(self):
        self.assertEqual(self.client.post('/v1/agent/audio', headers={**self.auth_header, 'Content-Type':'audio/wav'}, content=b'bad').status_code, 400)
        self.assertEqual(self.client.post('/v1/agent/turn', headers=self.auth_header, json={'command': ' '}).status_code, 422)
        self.assertEqual(self.client.post('/v1/agent/turn', headers=self.auth_header, content=b'x' * (api.MAX_JSON_BYTES + 1)).status_code, 415)
        self.assertEqual(self.client.post('/v1/agent/turn', headers={**self.auth_header, 'Content-Type':'application/json'}, content=b'x' * (api.MAX_JSON_BYTES + 1)).status_code, 413)
        self.execute.assert_not_called()

if __name__ == '__main__': unittest.main()
