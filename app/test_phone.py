"""Tests for phone.py that need no network: python3 -m unittest test_phone"""
import base64
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import app
import phone
from test_app import AREA, KIKI, LORE, RAIN, ZONE_ITEMS, warn

LAT, LON = 35.123456, 139.654321


def b64u_decode(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


BEAR = {'km': 0.11, 'time': '2026/09/20 06:10', 'kind': '目撃', 'animal': 'ツキノワグマ', 'heads': 1, 'town': '秋田市',
        'source': app.WILDLIFE_SETS['05']['name'], 'short': '秋田県 クマダス', 'data_until': '2026/09/28',
        'link': 'https://maps.gsi.go.jp/#16/39.701000/140.100000/&base=std'}


def wild_for(bear):
    return {'sources': [{'name': app.WILDLIFE_SETS['05']['name'], 'map': 'https://kumadas.net/',
                         'map_name': 'クマダス（秋田県）'}],
            'notify': {'animals': ['ツキノワグマ'], 'radius_km': 1.0, 'days': 60, 'count': 1 if bear else 0,
                       'newest': bear}}


def out_for(w, zone_items=ZONE_ITEMS, demo=None, checked='2026-11-03 23:50', bear=None):
    """here() output built from the test_app fixtures (no network)."""
    zones = {'items': zone_items, 'link': f'https://disaportal.gsi.go.jp/maps/?ll={LAT},{LON}'}
    kiki = [{**k, 'link': f"https://www.jma.go.jp/bosai/risk/#elements:{k['key']}"} for k in KIKI]
    w = {**w, 'link': 'https://www.jma.go.jp/bosai/warning/', 'flood_link': 'https://www.jma.go.jp/bosai/flood/'}
    wild = wild_for(bear)
    return {'point': {'lat': LAT, 'lon': LON}, 'checked_at': checked, 'area': AREA, 'warning': w,
            'kikikuru': kiki, 'zones': zones, 'demo': demo, 'wildlife': wild,
            'notice': app.build_notice(AREA, w, zone_items, kiki, RAIN, LORE, None, wild)}


@unittest.skipUnless(shutil.which('openssl'), 'openssl is not installed')
class SignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.key = os.path.join(cls.dir, 'key.pem')
        cls.pub = os.path.join(cls.dir, 'pub.pem')
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048',
                        '-out', cls.key], check=True, capture_output=True)
        subprocess.run(['openssl', 'pkey', '-in', cls.key, '-pubout', '-out', cls.pub], check=True, capture_output=True)
        with open(cls.key) as f:
            cls.pem = f.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def verify(self, data, sig):
        path = os.path.join(self.dir, 'sig')
        with open(path, 'wb') as f:
            f.write(sig)
        r = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', self.pub, '-signature', path],
                           input=data, capture_output=True)
        return r.returncode == 0

    def test_pkcs8_signature_verifies(self):
        self.assertIn('BEGIN PRIVATE KEY', self.pem)
        data = 'いまいる場所'.encode()
        sig = phone.rs256(self.pem, data)
        self.assertEqual(len(sig), 256)
        self.assertTrue(self.verify(data, sig))
        self.assertFalse(self.verify(data + b'x', sig))

    def test_pkcs1_signature_verifies(self):
        r = subprocess.run(['openssl', 'rsa', '-in', self.key, '-traditional'], check=True, capture_output=True)
        pem1 = r.stdout.decode()
        self.assertIn('BEGIN RSA PRIVATE KEY', pem1)
        self.assertEqual(phone.rs256(pem1, b'abc'), phone.rs256(self.pem, b'abc'))

    def test_jwt_assertion(self):
        g = phone.Google({'project_id': 'p', 'client_email': 'sa@p.iam.gserviceaccount.com', 'private_key': self.pem,
                          'token_uri': 'https://oauth2.googleapis.com/token', 'private_key_id': 'k1'})
        jwt = g.assertion(1_800_000_000)
        head, claims, sig = jwt.split('.')
        self.assertEqual(json.loads(b64u_decode(head)), {'alg': 'RS256', 'typ': 'JWT', 'kid': 'k1'})
        c = json.loads(b64u_decode(claims))
        self.assertEqual(c['aud'], 'https://oauth2.googleapis.com/token')
        self.assertEqual(c['exp'] - c['iat'], 3600)
        self.assertEqual(c['scope'].split(), phone.SCOPES)
        self.assertTrue(self.verify(f'{head}.{claims}'.encode(), b64u_decode(sig)))

    def test_not_a_key(self):
        with self.assertRaises(ValueError):
            phone.rs256('nothing here', b'x')


class FakeHttp:
    """Stands in for phone.http: records calls and answers from a list of (status, body)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers or {}, data))
        return self.answers.pop(0)


def google():
    g = phone.Google({'project_id': 'p', 'client_email': 'e', 'private_key': 'k',
                      'token_uri': 'https://oauth2.googleapis.com/token'})
    g._token, g._until = 'T', 2 ** 40
    return g


class GoogleTest(unittest.TestCase):
    def test_token_is_cached(self):
        g = google()
        g._token = None
        fake = FakeHttp((200, {'access_token': 'A', 'expires_in': 3599}))
        with mock.patch.object(phone, 'http', fake), mock.patch.object(g, 'assertion', return_value='j.w.t'):
            self.assertEqual(g.token(), 'A')
            self.assertEqual(g.token(), 'A')
        self.assertEqual(len(fake.calls), 1)
        self.assertIn(b'assertion=j.w.t', fake.calls[0][3])

    def test_bad_token_uri(self):
        with self.assertRaises(ValueError):
            phone.Google({'project_id': 'p', 'client_email': 'e', 'private_key': 'k', 'token_uri': 'https://x.example/'})

    def test_position(self):
        fields = {'lat': {'doubleValue': 35.1}, 'lon': {'doubleValue': 139.6}, 'accuracy': {'doubleValue': 12},
                  'at': {'integerValue': '1800000000'}, 'token': {'stringValue': 'fcm'}}
        fake = FakeHttp((200, {'fields': fields}), (404, None))
        with mock.patch.object(phone, 'http', fake):
            g = google()
            self.assertEqual(g.position('dev'), {'lat': 35.1, 'lon': 139.6, 'accuracy': 12.0, 'at': 1800000000,
                                                 'token': 'fcm'})
            self.assertIsNone(g.position('dev'))
        self.assertTrue(fake.calls[0][1].endswith('/projects/p/databases/(default)/documents/devices/dev'))
        self.assertEqual(fake.calls[0][2]['Authorization'], 'Bearer T')

    def test_send(self):
        gone = {'error': {'status': 'NOT_FOUND', 'details': [{'errorCode': 'UNREGISTERED'}]}}
        fake = FakeHttp((200, {'name': 'x'}), (404, gone), (500, {'error': {'status': 'INTERNAL'}}))
        with mock.patch.object(phone, 'http', fake):
            g = google()
            self.assertEqual(g.send('fcm', {'title': 't'}), 'sent')
            self.assertEqual(g.send('fcm', {'title': 't'}), 'gone')
            self.assertEqual(g.send('fcm', {'title': 't'}), 'error 500 INTERNAL')
        msg = json.loads(fake.calls[0][3])['message']
        self.assertEqual(msg['android']['priority'], 'HIGH')
        self.assertNotIn('notification', msg)
        self.assertTrue(fake.calls[0][1].endswith('/v1/projects/p/messages:send'))


class PushTest(unittest.TestCase):
    def test_key_ignores_reissue_but_not_level_or_place(self):
        a = out_for(warn(('49', 'landslide', 4)))
        w = warn(('49', 'landslide', 4))
        w['warnings'][0]['reportDatetime'] = 't2'
        self.assertNotEqual(a['notice']['key'], out_for(w)['notice']['key'])
        self.assertEqual(phone.push_key(a), phone.push_key(out_for(w)))
        self.assertNotEqual(phone.push_key(a), phone.push_key(out_for(warn(('50', 'landslide', 5)))))
        moved = [{**z, 'in': False} for z in ZONE_ITEMS]
        self.assertNotEqual(phone.push_key(a), phone.push_key(out_for(w, moved)))
        self.assertNotEqual(phone.push_key(a), phone.push_key(out_for(w, demo={'hazard': 'landslide', 'level': 4})))

    def test_links_follow_the_hazards(self):
        names = lambda o: [n for n, _ in phone.links_for(o)]
        self.assertEqual(names(out_for(warn(('49', 'landslide', 4)))),
                         ['気象庁の警報・注意報', '土砂キキクル', '重ねるハザードマップ'])
        flood = {'warnings': [], 'flood': [{'code': '52', 'hazard': 'flood', 'level': 4, 'name': 'x',
                                            'reportDatetime': 't', 'group1': True}]}
        self.assertEqual(names(out_for(flood)), ['気象庁の警報・注意報', '指定河川洪水予報'])

    def test_message_is_strings_and_fits(self):
        o = out_for(warn(('49', 'landslide', 4)))
        m = phone.message_for(o, age=3 * 3600 + 5 * 60)
        self.assertTrue(all(isinstance(v, str) for v in m.values()))
        self.assertLessEqual(len(json.dumps(m, ensure_ascii=False).encode()), phone.DATA_LIMIT)
        self.assertEqual(m['title'], '⛰️ レベル4 土砂災害（どしゃさいがい）に気をつけて')
        # the body says the facts first: the warning by its official name (the level in words, not only the
        # color), then what is at this spot; then it names the official pages, in the buttons' order
        # the official name again in hiragana and in the JMA's own English (no new fact)
        facts = ['千代田区にレベル４土砂災害危険警報', 'ちよだくに れべる4 どしゃさいがい きけんけいほう',
                 'Chiyoda City: Level 4 Urgent Warning (Landslide)', 'ここ　土石流の警戒区域',
                 'ここ　土砂キキクル「危険（紫）警戒レベル４相当」', '雨　いま 30～50 mm/h、1時間後 50～80 mm/h',
                 '伝承碑　2.5km 先（土砂災害）']
        # the pages say what they show, then their official names
        pages = ['千代田区の警報・注意報（気象庁）', 'この場所の土砂災害の危険度分布の地図（土砂キキクル・気象庁）',
                 'この場所のハザードマップ（重ねるハザードマップ・国土交通省）']
        self.assertEqual(m['body'].split('\n'), facts + pages + ['位置は 3 時間 5 分前のもの'])
        self.assertEqual(m['summary'], '千代田区にレベル４土砂災害危険警報')  # the folded line says the level in words
        # the app lists every page under the body's names, and shows the facts and the notes above them
        self.assertEqual([n for n, _ in json.loads(m['links'])], pages)
        self.assertEqual(m['notes'].split('\n'), facts + ['位置は 3 時間 5 分前のもの'])
        self.assertEqual(m['area'], '千代田区')
        self.assertEqual([b for b, _ in json.loads(m['buttons'])], ['警報（気象庁）', '土砂災害の地図'])
        self.assertEqual(m['level'], '4')

    def test_demo_note_stays_last(self):
        o = out_for(app.add_demo(warn(), ('landslide', 4), datetime(2026, 11, 3, 23, 50)),
                    demo={'hazard': 'landslide', 'level': 4})
        m = phone.message_for(o, age=3 * 3600)
        self.assertEqual(m['title'], '⛰️ レベル4 土砂災害（どしゃさいがい）に気をつけて（試し）')
        lines = m['body'].split('\n')
        self.assertEqual(lines[0], '千代田区にレベル４土砂災害危険警報（試し）')
        # the readings carry the mark too, so no one takes the made-up warning for a real one
        self.assertEqual(lines[1], 'ちよだくに れべる4 どしゃさいがい きけんけいほう（ためし）')
        self.assertEqual(lines[2], 'Chiyoda City: Level 4 Urgent Warning (Landslide) (TEST)')
        self.assertEqual(m['summary'], lines[0])
        self.assertIn('千代田区の警報・注意報（気象庁）', lines)
        self.assertEqual(lines[-2], '警報は試しの作りもの。ほかは本物')
        self.assertTrue(lines[-1].startswith('位置は '))
        self.assertEqual(m['demo'], '1')

    def test_bear_alone(self):
        o = out_for(warn(), bear=BEAR)
        self.assertEqual([n for n, _ in phone.links_for(o)], ['クマダス（秋田県）', '記録の地点（地理院地図）'])
        m = phone.message_for(o)
        self.assertEqual(m['title'], '🐻 ツキノワグマ（つきのわぐま）に気をつけて')
        self.assertEqual(m['body'].split('\n'), ['ツキノワグマ　1km 以内に 1 件（60 日以内）',
                                                  'つきのわぐまの きろく 1けん（1km より ちかく、60にち の あいだ）',
                                                  'Asian black bear: 1 record within 1 km (last 60 days)',
                                                  '出没の記録の地図（クマダス・秋田県）', '記録の地点の地図（地理院地図）'])
        self.assertEqual(m['summary'], 'ツキノワグマ　1km 以内に 1 件（60 日以内）')
        self.assertEqual([b for b, _ in json.loads(m['buttons'])], ['出没の地図', '記録の地点'])
        self.assertEqual(m['level'], '0')
        self.assertEqual(json.loads(m['links'])[1][1], BEAR['link'])
        o['warning'] = None  # the warning part failed; the bear notice still goes out
        self.assertEqual(len(phone.links_for(o)), 2)

    def test_bear_key_follows_the_record(self):
        a = phone.push_key(out_for(warn(), bear=BEAR))
        self.assertEqual(a, phone.push_key(out_for(warn(), bear={**BEAR, 'km': 0.4})))  # the phone moved a little
        self.assertNotEqual(a, phone.push_key(out_for(warn(), bear={**BEAR, 'time': '2026/09/29 17:00'})))
        self.assertNotIn('bear:', phone.push_key(out_for(warn(('49', 'landslide', 4)))))

    def test_bear_with_warning(self):
        o = out_for(warn(('49', 'landslide', 4)), bear=BEAR)
        self.assertEqual([n for n, _ in phone.links_for(o)],
                         ['気象庁の警報・注意報', '土砂キキクル', '重ねるハザードマップ', 'クマダス（秋田県）', '記録の地点（地理院地図）'])
        self.assertTrue(phone.push_key(o).endswith(f"|bear:{BEAR['time']}@{BEAR['link']}"))

    def test_no_instructions(self):
        m = phone.message_for(out_for(warn(('03', 'rain', 3), ('49', 'landslide', 4))), age=9 * 3600)
        for word in ('避難', 'してください', 'しましょう', 'おそれ', '予想', '命'):
            self.assertNotIn(word, m['body'])
            self.assertNotIn(word, m['notes'])
        self.assertTrue(m['body'].startswith('千代田区にレベル４土砂災害危険警報、レベル３大雨警報\n'))

    def test_too_long_drops_pages_then_facts(self):
        o = out_for(warn(('49', 'landslide', 4)))
        long_tail = ['あ' * 400]  # the tail always stays; the pages go first (all but one), then the last facts
        o['notice'] = {**o['notice'], 'notes': long_tail}
        m = phone.message_for(o)
        self.assertLessEqual(len(json.dumps(m, ensure_ascii=False).encode()), phone.DATA_LIMIT)
        # the warning's name and its readings stay before the facts at the spot
        self.assertEqual(m['body'].split('\n'), ['千代田区にレベル４土砂災害危険警報',
                                                  'ちよだくに れべる4 どしゃさいがい きけんけいほう',
                                                  'Chiyoda City: Level 4 Urgent Warning (Landslide)',
                                                  'ここ　土石流の警戒区域',
                                                  '千代田区の警報・注意報（気象庁）'] + long_tail)
        self.assertEqual(m['notes'].split('\n'), m['body'].split('\n')[:4] + long_tail)


class FakeGoogle:
    def __init__(self, pos):
        self.pos, self.sent = pos, []

    def position(self, device):
        return self.pos

    def send(self, token, data):
        self.sent.append((token, data))
        return 'sent'


class StateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        p = mock.patch.object(phone, 'STATE', os.path.join(self.dir, '.phone', 'phone.json'))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir)
        self.state = {'devices': {'mine': {'id': 'dev'}}, 'last': {}}

    def google_services(self, package=phone.PACKAGE):
        path = os.path.join(self.dir, 'google-services.json')
        with open(path, 'w') as f:
            json.dump({'project_info': {'project_number': '123', 'project_id': 'proj'},
                       'client': [{'client_info': {'mobilesdk_app_id': '1:123:android:abc',
                                                   'android_client_info': {'package_name': package}},
                                   'api_key': [{'current_key': 'AIza-test'}]}]}, f)
        return path

    def test_pair(self):
        setup = json.loads(phone.pair(self.google_services(), 'mine'))
        self.assertEqual({k: setup[k] for k in ('v', 'projectId', 'senderId', 'appId', 'apiKey')},
                         {'v': 1, 'projectId': 'proj', 'senderId': '123', 'appId': '1:123:android:abc',
                          'apiKey': 'AIza-test'})
        self.assertGreaterEqual(len(setup['device']), 40)
        state = phone.load_state()
        self.assertEqual(state['devices']['mine']['id'], setup['device'])
        self.assertEqual(stat.S_IMODE(os.stat(phone.STATE).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(phone.STATE)).st_mode), 0o700)
        self.assertNotEqual(json.loads(phone.pair(self.google_services(), 'mine'))['device'], setup['device'])

    def test_pair_refuses(self):
        with self.assertRaises(ValueError):
            phone.pair(self.google_services('other.app'), 'mine')
        with self.assertRaises(ValueError):
            phone.pair(self.google_services(), '../x')

    def check(self, g, out, **kw):
        with mock.patch.object(phone, 'here', return_value=out) as h:
            text = phone.check_device(g, self.state, 'mine', **kw)
        return text, h

    def test_push_once_then_again_after_quiet(self):
        g = FakeGoogle({'lat': LAT, 'lon': LON, 'at': 0, 'token': 'fcm'})
        loud = out_for(warn(('49', 'landslide', 4)))
        self.assertTrue(self.check(g, loud)[0].startswith('送りました'))
        self.assertEqual(self.check(g, loud)[0], '知らせは送ったものから変わっていません')
        self.assertTrue(self.check(g, loud, force=True)[0].startswith('送りました'))
        self.assertEqual(len(g.sent), 2)
        self.assertEqual(g.sent[0][0], 'fcm')
        self.check(g, {**loud, 'notice': {'notify': False}})
        self.assertTrue(self.check(g, loud)[0].startswith('送りました'))
        self.assertEqual(len(g.sent), 3)

    def test_nothing_without_position_or_outside_japan(self):
        for pos in (None, {'lat': LAT, 'lon': LON}, {'lat': 10.0, 'lon': 100.0, 'token': 'fcm'}):
            text, h = self.check(FakeGoogle(pos), out_for(warn(('49', 'landslide', 4))))
            h.assert_not_called()

    def test_log_has_no_position(self):
        phone.save_state({'devices': {'mine': {'id': 'dev'}}, 'last': {}})
        g = FakeGoogle({'lat': LAT, 'lon': LON, 'at': 0, 'token': 'fcm'})
        buf = io.StringIO()
        with mock.patch.object(phone, 'here', return_value=out_for(warn(('49', 'landslide', 4)))), \
                contextlib.redirect_stdout(buf):
            phone.check_all(g, phone.load_state())
        self.assertIn('mine: 送りました', buf.getvalue())
        for text in (str(LAT), str(LON), '35.12', '139.65'):
            self.assertNotIn(text, buf.getvalue())
        with open(phone.STATE) as f:
            self.assertNotIn('35.12', f.read())


if __name__ == '__main__':
    unittest.main()
