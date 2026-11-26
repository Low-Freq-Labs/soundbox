"""Official release metadata is bounded and cannot select a foreign installer."""
from copy import deepcopy
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from soundbox import release_updates as updates


def payload():
    tag = 'v1.0.0-build.180'
    return dict(draft=False, prerelease=False, tag_name=tag,
        html_url=f'{updates.REPOSITORY}/releases/tag/{tag}', assets=[dict(name=updates.ASSET,
            browser_download_url=f'{updates.REPOSITORY}/releases/download/{tag}/{updates.ASSET}',
            state='uploaded', size=123456)])


class Response(BytesIO):
    status = 200
    def __init__(self, value, *, content_type='application/json', length=None):
        super().__init__(value)
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        if length is not None:
            self.headers['Content-Length'] = length

    def geturl(self):
        return updates.API


class ReleaseUpdates(unittest.TestCase):
    installed = updates.Build('1.0.0', 179, 'personal')

    def parse(self, value):
        return updates.parse_release(json.dumps(value).encode(), self.installed)

    def test_exact_official_installer_is_offered_only_for_newer_build(self):
        result = self.parse(payload())
        self.assertEqual((result.version, result.build), ('1.0.0', 180))
        self.assertEqual(result.download_url, payload()['assets'][0]['browser_download_url'])
        self.assertIsNone(updates.parse_release(json.dumps(payload()).encode(), updates.Build('1.0.0', 180, 'personal')))
        self.assertIsNone(updates.parse_release(json.dumps(payload()).encode(), updates.Build('1.1.0', 1, 'personal')))

    def test_draft_prerelease_foreign_urls_duplicate_assets_and_malformed_identity_refuse(self):
        mutations = [lambda v: v.update(draft=True), lambda v: v.update(prerelease=True),
            lambda v: v.update(draft=0), lambda v: v.update(tag_name='v1.0.0-build.0180'),
            lambda v: v.update(html_url='https://github.com/foreign/soundbox/releases/tag/v1.0.0-build.180'),
            lambda v: v['assets'].append(deepcopy(v['assets'][0])),
            lambda v: v['assets'][0].update(size=True),
            lambda v: v['assets'][0].update(browser_download_url=v['assets'][0]['browser_download_url']+'?redirect=evil'),
            lambda v: v['assets'][0].update(state='new')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = payload(); mutate(value)
                with self.assertRaises(ValueError):
                    self.parse(value)

    def test_duplicate_json_fields_and_large_body_refuse(self):
        for raw in (b'{"draft":true,"draft":false}', b'{"unknown":NaN}', b' '*(updates.MAX_BYTES+1)):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                updates.parse_release(raw, self.installed)

    def test_public_channel_refuses_before_network_or_personal_asset_selection(self):
        with mock.patch.object(updates, 'build_opener') as opener:
            with self.assertRaises(ValueError):
                updates.latest_release(updates.Build('1.0.0', 179, 'public'))
        opener.assert_not_called()
        with self.assertRaises(ValueError):
            updates.parse_release(json.dumps(payload()).encode(), updates.Build('1.0.0', 179, 'public'))

    def test_https_request_has_fixed_origin_system_trust_no_credentials_and_bounded_read(self):
        response = Response(json.dumps(payload()).encode())
        opener = mock.Mock(); opener.open.return_value = response
        with mock.patch.object(updates, 'build_opener', return_value=opener) as build:
            self.assertEqual(updates.latest_release(self.installed).build, 180)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, updates.API)
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(opener.open.call_args.kwargs, dict(timeout=updates.TIMEOUT))
        import truststore
        self.assertIsInstance(build.call_args.args[0]._context, truststore.SSLContext)
        self.assertIsInstance(build.call_args.args[1], updates._NoRedirect)

    def test_redirects_oversized_stream_wrong_type_and_expired_deadline_refuse(self):
        with self.assertRaises(ValueError):
            updates._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid')
        for response in (Response(b'x'*(updates.MAX_BYTES+1)), Response(b'{}', content_type='text/html'),
                         Response(b'{}', length=str(updates.MAX_BYTES+1))):
            opener = mock.Mock(); opener.open.return_value = response
            with self.subTest(headers=response.headers), mock.patch.object(updates, 'build_opener', return_value=opener):
                with self.assertRaises(ValueError):
                    updates.latest_release(self.installed)
        opener = mock.Mock(); opener.open.return_value = Response(b'{}')
        with mock.patch.object(updates, 'build_opener', return_value=opener), \
                mock.patch.object(updates.time, 'monotonic', side_effect=[1, 99]):
            with self.assertRaises(TimeoutError):
                updates.latest_release(self.installed)

    def test_no_release_is_unavailable_without_inventing_a_newer_version(self):
        opener = mock.Mock(); opener.open.side_effect = HTTPError(updates.API, 404, '', {}, None)
        with mock.patch.object(updates, 'build_opener', return_value=opener):
            self.assertIsNone(updates.latest_release(self.installed))

    def test_running_packaged_build_uses_its_own_profile_version_and_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'Soundbox.app/Contents'; (root/'MacOS').mkdir(parents=True)
            (root/'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='com.lowfreqlabs.soundbox',
                CFBundleShortVersionString='1.0.0', CFBundleVersion='179', SoundboxBuildProfile='public')))
            with mock.patch.object(updates.sys, 'frozen', True, create=True), \
                    mock.patch.object(updates.sys, 'executable', str(root/'MacOS/Soundbox')):
                self.assertEqual(updates.current_build(), updates.Build('1.0.0', 179, 'public'))


if __name__ == '__main__':
    unittest.main()
