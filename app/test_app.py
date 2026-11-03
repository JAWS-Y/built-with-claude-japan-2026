"""Tests that need no network: python3 -m unittest test_app"""
import http.client
import math
import struct
import threading
import unittest
import zlib
from http.server import ThreadingHTTPServer

import app
from png import Png, _paeth


def make_png(rows, ctype=6, filters=None):
    """PNG of 8-bit rows (list of lists of pixel tuples), each row with its own filter type."""
    n = {2: 3, 6: 4}[ctype]
    height, width = len(rows), len(rows[0])
    raw, prev = b'', bytes(width * n)
    for y, row in enumerate(rows):
        line = bytes(v for px in row for v in px)
        f = (filters or [0] * height)[y]
        enc = bytearray()
        for i, v in enumerate(line):
            a = line[i - n] if i >= n else 0
            b = prev[i]
            c = prev[i - n] if i >= n else 0
            pred = {0: 0, 1: a, 2: b, 3: (a + b) >> 1, 4: _paeth(a, b, c)}[f]
            enc.append((v - pred) & 255)
        raw += bytes([f]) + bytes(enc)
        prev = line

    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, ctype, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class PngTest(unittest.TestCase):
    def test_all_filters(self):
        rows = [[(x * 40, y * 50, (x + y) * 20, 255 - x) for x in range(5)] for y in range(5)]
        p = Png(make_png(rows, filters=[0, 1, 2, 3, 4]))
        for y in (4, 0, 2):  # out of order on purpose (rows are unfiltered lazily)
            for x in range(5):
                self.assertEqual(p.rgba(x, y), rows[y][x])

    def test_rgb(self):
        rows = [[(1, 2, 3), (4, 5, 6)]]
        self.assertEqual(Png(make_png(rows, ctype=2)).rgba(1, 0), (4, 5, 6, 255))

    def test_outside(self):
        with self.assertRaises(IndexError):
            Png(make_png([[(0, 0, 0, 0)]])).rgba(1, 0)


class GeometryTest(unittest.TestCase):
    def test_tile_px_inverse(self):
        lat, lon, z = 35.71, 139.82, 10
        tx, ty, px, py = app.tile_px(lat, lon, z)
        n = 2 ** z
        lon_w = (tx + px / 256) / n * 360 - 180
        lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + py / 256) / n))))
        self.assertAlmostEqual(lon_w, lon, delta=360 / n / 256)
        self.assertAlmostEqual(lat_n, lat, delta=0.002)
        self.assertEqual(app.tile_px(0, 0, 1), (1, 1, 0, 0))

    def test_polygon_with_hole(self):
        outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
        g = {'type': 'Polygon', 'coordinates': [outer, hole]}
        self.assertTrue(app.in_geometry(g, 2, 2))
        self.assertFalse(app.in_geometry(g, 5, 5))
        self.assertFalse(app.in_geometry(g, 11, 5))

    def test_multipolygon_feature_collection(self):
        sq = lambda x0: [[[x0, 0], [x0 + 1, 0], [x0 + 1, 1], [x0, 1], [x0, 0]]]
        fc = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': {'type': 'MultiPolygon', 'coordinates': [sq(0), sq(5)]}}]}
        self.assertTrue(app.in_features(fc, 5.5, 0.5))
        self.assertFalse(app.in_features(fc, 3, 0.5))

    def test_km(self):
        self.assertAlmostEqual(app.km(35, 139, 36, 139), 111.2, delta=0.2)


class ColourTest(unittest.TestCase):
    def test_nearest(self):
        self.assertIsNone(app.nearest((255, 40, 0, 0), app.RISK_LEGEND))
        self.assertEqual(app.nearest((168, 2, 171, 255), app.RISK_LEGEND, 60)[0], 4)
        self.assertIsNone(app.nearest((0, 255, 0, 255), app.RISK_LEGEND, 60))

    def test_zone_value(self):
        self.assertEqual(app.zone_value('depth', (255, 216, 192, 255)), (True, '0.5～3m'))
        self.assertEqual(app.zone_value('depth_naisui', (247, 245, 169, 255)), (True, '0.1～0.5m'))
        self.assertEqual(app.zone_value('sediment', (165, 0, 33, 255)), (True, '特別警戒区域'))
        self.assertEqual(app.zone_value('sediment', (250, 230, 0, 255)), (True, '警戒区域'))
        self.assertEqual(app.zone_value('presence', (1, 2, 3, 200)), (True, None))
        self.assertEqual(app.zone_value('depth', (0, 0, 0, 0)), (False, None))
        self.assertEqual(app.zone_value('depth', None), (False, None))


C20, C10 = '1310100', '130011'


def report(time, kinds, key='class20Items', code=C20):
    return {'reportDatetime': time, 'warning': {key: [{'areaCode': code, 'kinds': kinds},
                                                      {'areaCode': 'other', 'kinds': [{'code': '33', 'status': '発表'}]}]}}


class WarningTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(app.warning_name('landslide', 4), 'レベル４土砂災害危険警報')
        self.assertEqual(app.warning_name('rain', 5), 'レベル５大雨特別警報')
        self.assertEqual(app.warning_name('tide', 3), 'レベル３高潮警報')
        self.assertEqual(app.warning_name('flood', 2), 'レベル２氾濫注意報')
        self.assertEqual(app.warning_name('wind', 3), '暴風警報')
        self.assertEqual(app.warning_name('wind', 5), '暴風特別警報')
        self.assertEqual(app.warning_name('wind', 2), '強風注意報')
        self.assertEqual(app.warning_name('wind_snow', 3), '暴風雪警報')
        self.assertEqual(app.warning_name('snow', 2), '大雪注意報')
        self.assertEqual(app.warning_name('thunder', 2), '雷注意報')

    def test_latest_report_wins(self):
        reps = [report('2026-11-03T10:00:00+09:00', [{'code': '03', 'status': '発表'}, {'code': '15', 'status': '発表'}]),
                report('2026-11-03T12:00:00+09:00', [{'code': '03', 'status': '解除'}, {'code': '49', 'status': '発表'},
                                                     {'code': '15', 'status': '継続'}])]
        got, unknown = app.decode_warnings(reps, C20, C10)
        self.assertEqual([w['name'] for w in got], ['レベル４土砂災害危険警報', '強風注意報'])
        self.assertEqual(got[0]['level'], 4)
        self.assertTrue(got[0]['group1'])
        self.assertFalse(got[1]['group1'])
        self.assertEqual(unknown, [])

    def test_none_status_and_unknown(self):
        reps = [report('t1', [{'status': '発表警報・注意報はなし'}]),
                report('t1', [{'code': '99', 'status': '発表'}, {'code': '98', 'status': '解除'}])]
        got, unknown = app.decode_warnings(reps, C20, C10)
        self.assertEqual(got, [])
        self.assertEqual(unknown, ['99'])

    def test_class10_fallback(self):
        reps = [report('t1', [{'code': '08', 'status': '発表'}], key='class10Items', code=C10)]
        got, _ = app.decode_warnings(reps, C20, C10)
        self.assertEqual([w['name'] for w in got], ['レベル３高潮警報'])

    def test_flood(self):
        entries = [
            {'item': {'code': '41', 'status': '発表', 'riverName': '荒川'}, 'reportDatetime': 't2',
             'class20Codes': [C20], 'class10Codes': []},
            {'item': {'code': '31', 'status': '解除'}, 'reportDatetime': 't2', 'class20Codes': [C20]},
            {'item': {'code': '51', 'status': '発表'}, 'reportDatetime': 't2', 'class20Codes': ['other']},
            {'item': {'code': '22', 'status': '発表'}, 'reportDatetime': 't2', 'class10Codes': [C10]},
        ]
        got = app.decode_flood(entries, C20, C10)
        self.assertEqual([w['name'] for w in got], ['レベル４氾濫危険警報', 'レベル２氾濫注意報'])
        self.assertEqual(got[0]['detail'], {'riverName': '荒川'})


AREA = {'class20': {'code': C20, 'name': '千代田区'}}
ZONE_ITEMS = [{'key': 'dosekiryu', 'label': '土砂災害警戒区域（土石流）', 'in': True, 'value': '警戒区域'},
              {'key': 'naisui', 'label': '内水浸水想定区域', 'in': True, 'value': '0.1～0.5m'},
              {'key': 'kyukeisha', 'label': '土砂災害警戒区域（急傾斜地の崩壊）', 'in': False, 'value': None}]
KIKI = [{'key': 'land', 'label': '土砂キキクル（土砂災害の危険度分布）', 'level': 4, 'meaning': '危険（紫）警戒レベル４相当',
         'time': '11/03 12:00'},
        {'key': 'inund', 'label': '浸水キキクル（大雨による浸水の危険度分布）', 'level': 2, 'meaning': '注意（黄）警戒レベル２相当',
         'time': '11/03 12:00'}]
RAIN = {'now': {'time': '11/03 12:00', 'rain': '30～50 mm/h'}, 'plus60': {'time': '11/03 13:00', 'rain': '50～80 mm/h'}}
LORE = [{'km': 1.2, 'kind': '洪水', 'name': 'A'}, {'km': 2.5, 'kind': '土砂災害', 'name': 'B'}]


def warn(*items):
    ws = [{'code': c, 'hazard': h, 'level': lv, 'name': app.warning_name(h, lv), 'reportDatetime': 't',
           'group1': h in app.GROUP1} for c, h, lv in items]
    return {'warnings': ws, 'flood': []}


class NoticeTest(unittest.TestCase):
    def test_landslide_notice_uses_matching_facts_only(self):
        n = app.build_notice(AREA, warn(('49', 'landslide', 4)), ZONE_ITEMS, KIKI, RAIN, LORE)
        self.assertTrue(n['notify'])
        self.assertIn('千代田区にレベル４土砂災害危険警報が出ています', n['body'])
        self.assertIn('土砂災害警戒区域（土石流）（警戒区域）', n['body'])
        self.assertNotIn('内水', n['body'])
        self.assertNotIn('急傾斜', n['body'])
        self.assertIn('土砂キキクルはこの地点で「危険（紫）警戒レベル４相当」', n['body'])
        self.assertNotIn('浸水キキクル', n['body'])
        self.assertIn('いま 30～50 mm/h、60分後 50～80 mm/h', n['body'])
        self.assertIn('土砂災害の伝承碑「B」', n['body'])
        self.assertTrue(n['place_found'])

    def test_no_instructions(self):
        n = app.build_notice(AREA, warn(('03', 'rain', 3), ('49', 'landslide', 4)), ZONE_ITEMS, KIKI, RAIN, LORE)
        for word in ('避難', 'してください', 'しましょう', 'おそれ', '予想'):
            self.assertNotIn(word, n['body'])

    def test_no_notice_below_level3_or_other_hazards(self):
        self.assertFalse(app.build_notice(AREA, warn(('10', 'rain', 2)), ZONE_ITEMS, KIKI, RAIN, LORE)['notify'])
        self.assertFalse(app.build_notice(AREA, warn(('05', 'wind', 3)), ZONE_ITEMS, KIKI, RAIN, LORE)['notify'])
        self.assertFalse(app.build_notice(AREA, {'warnings': [], 'flood': []}, [], [], None, [])['notify'])

    def test_key_changes_with_new_report(self):
        a = app.build_notice(AREA, warn(('03', 'rain', 3)), [], [], None, [])
        w = warn(('03', 'rain', 3))
        w['warnings'][0]['reportDatetime'] = 't2'
        self.assertNotEqual(a['key'], app.build_notice(AREA, w, [], [], None, [])['key'])


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path, host=None):
        c = http.client.HTTPConnection('127.0.0.1', self.srv.server_address[1], timeout=5)
        c.request('GET', path, headers={'Host': host or f'127.0.0.1:{self.srv.server_address[1]}'})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, body

    def test_page(self):
        status, body = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn('現在地で調べる'.encode(), body)

    def test_bad_requests(self):
        self.assertEqual(self.get('/api/here?lat=1&lon=1')[0], 400)
        self.assertEqual(self.get('/api/here?lat=x&lon=1')[0], 400)
        self.assertEqual(self.get('/nothing')[0], 404)

    def test_other_host_refused(self):
        self.assertEqual(self.get('/', host='example.com')[0], 403)


if __name__ == '__main__':
    unittest.main()
