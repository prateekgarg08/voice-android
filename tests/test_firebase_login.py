import io
import json
import unittest
from unittest.mock import patch
from firebase_login_test import test_login as sign_in


class ContextResponse(io.BytesIO):
    pass


class FirebaseLoginTests(unittest.TestCase):
    def test_signs_in_and_uses_id_token_as_bearer(self):
        requests = []
        def fake_open(request, timeout):
            requests.append(request)
            body = {'idToken': 'id-token'} if len(requests) == 1 else {'uid': 'user-1'}
            return ContextResponse(json.dumps(body).encode())
        with patch('firebase_login_test.urllib.request.urlopen', side_effect=fake_open):
            self.assertEqual(sign_in('web-api-key', 'person@example.com', 'password'), {'uid': 'user-1'})
        body = json.loads(requests[0].data)
        self.assertEqual(body['email'], 'person@example.com')
        self.assertTrue(body['returnSecureToken'])
        self.assertEqual(requests[1].get_header('Authorization'), 'Bearer id-token')


if __name__ == '__main__': unittest.main()
