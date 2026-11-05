"""Tests that need no network: python3 -m unittest test_app"""
import http.client
import math
import struct
import threading
import unittest
import zlib
from http.server import ThreadingHTTPServer

import app
import compose
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
ROAD = {'radius_km': 3.0, 'count': 7, 'items': [{'km': 0.4, 'name': '〇〇アンダーパス', 'bureau': '国土交通省 関東地方整備局'}]}


def warn(*items):
    ws = [{'code': c, 'hazard': h, 'level': lv, 'name': app.warning_name(h, lv), 'reportDatetime': 't',
           'group1': h in app.GROUP1} for c, h, lv in items]
    return {'warnings': ws, 'flood': []}


class NoticeTest(unittest.TestCase):
    def test_landslide_notice_uses_matching_facts_only(self):
        n = app.build_notice(AREA, warn(('49', 'landslide', 4)), ZONE_ITEMS, KIKI, RAIN, LORE)
        self.assertTrue(n['notify'])
        lines = n['body'].split('\n')
        self.assertEqual(lines[0], 'ここ　土石流の警戒区域')
        self.assertEqual(lines[-1], '警報　レベル４土砂災害危険警報')  # the place first, the warning after
        self.assertEqual(n['title'], '土砂災害に気をつけて')  # the level is the color
        self.assertEqual(n['level'], 4)
        self.assertEqual(n['summary'], lines[0])
        self.assertNotIn('内水', n['body'])
        self.assertNotIn('急傾斜', n['body'])
        self.assertIn('ここ　土砂キキクル「危険（紫）警戒レベル４相当」', n['body'])
        self.assertNotIn('浸水キキクル', n['body'])
        self.assertIn('雨　いま 30～50 mm/h、1時間後 50～80 mm/h', n['body'])
        self.assertIn('伝承碑　2.5km 先（土砂災害）', n['body'])
        self.assertLessEqual(max(map(len, lines)), 32)  # one phone line, two at most
        self.assertTrue(n['place_found'])

    def test_rain_line(self):
        r = lambda now, later=None: {'now': {'rain': now}, **({'plus60': {'rain': later}} if later else {})}
        self.assertEqual(app.rain_line(r('降っていない')), 'いま なし')
        self.assertEqual(app.rain_line(r('降っていない', '降っていない')), 'いま なし、1時間後 なし')
        self.assertEqual(app.rain_line(r('1～5 mm/h', '降っていない')), 'いま 1～5 mm/h、1時間後 なし')
        self.assertEqual(app.rain_line(r('降っていない', '5～10 mm/h')), 'いま なし、1時間後 5～10 mm/h')

    def test_zone_short(self):
        self.assertEqual(app.zone_short({'label': '土砂災害警戒区域（土石流）', 'value': '特別警戒区域'}), '土石流の特別警戒区域')
        self.assertEqual(app.zone_short({'label': '洪水浸水想定区域（想定最大規模）', 'value': '0.5～3m'}), '洪水浸水想定区域 0.5～3m')
        self.assertEqual(app.zone_short({'label': '雪崩危険箇所', 'value': None}), '雪崩危険箇所')

    def test_no_instructions(self):
        n = app.build_notice(AREA, warn(('03', 'rain', 3), ('49', 'landslide', 4)), ZONE_ITEMS, KIKI, RAIN, LORE, ROAD)
        self.assertIn('冠水想定', n['body'])
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


KML_URL = 'https://example.mlit.go.jp/road/webmap/kml/13_tokyo_kansui_point.kml'
KML = '''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>A&amp;B 交差点</name><description><![CDATA[<table><tr><td>箇所表</td>
<td><a href="https://example.mlit.go.jp/a.pdf">PDF</a></td></tr></table>]]></description>
<Point><coordinates>139.7000,35.7000,0</coordinates></Point></Placemark>
<Placemark><name>近いアンダーパス</name><description><![CDATA[<a href="../attachment/b.pdf">PDF</a>]]></description>
<Point><coordinates>139.7010,35.7001</coordinates></Point></Placemark>
<Placemark><name>遠い所</name><description><![CDATA[<a href="javascript:alert(1)">x</a>]]></description>
<Point><coordinates>139.9000,35.9000,0</coordinates></Point></Placemark>
<Placemark><name>座標なし</name></Placemark>
</Document></kml>'''
RELM = {'1310100': {'name': '千代田区', 'ne': [35.71, 139.71], 'sw': [35.69, 139.69]},
        '1110000': {'name': '埼玉の所', 'ne': [35.95, 139.70], 'sw': [35.72, 139.40]},
        '4720100': {'name': '那覇市', 'ne': [26.25, 127.72], 'sw': [26.17, 127.65]},
        '0520100': {'name': '秋田市', 'ne': [39.80, 140.20], 'sw': [39.60, 140.00]}}


class RoadFloodTest(unittest.TestCase):
    def setUp(self):
        self.fetched, self.saved = [], (app.fetch, app.fetch_json)
        self.pages = {}

        def fetch(url, ttl):
            self.fetched.append(url)
            page = self.pages.get(url)
            if isinstance(page, Exception):
                raise page
            return page
        app.fetch, app.fetch_json = fetch, lambda url, ttl: RELM

    def tearDown(self):
        app.fetch, app.fetch_json = self.saved

    def test_parse(self):
        pts = app.parse_kansui(KML, KML_URL)
        self.assertEqual([p['name'] for p in pts], ['A&B 交差点', '近いアンダーパス', '遠い所'])
        self.assertEqual((pts[0]['lat'], pts[0]['lon']), (35.7, 139.7))
        self.assertEqual(pts[0]['pdf'], 'https://example.mlit.go.jp/a.pdf')
        self.assertEqual(pts[1]['pdf'], 'https://example.mlit.go.jp/road/webmap/attachment/b.pdf')
        self.assertEqual(pts[2]['pdf'], '')

    def test_every_prefecture_but_okinawa(self):
        self.assertEqual(sorted(app.KANSUI_PREFS), [f'{n:02d}' for n in range(1, 47)])
        self.assertEqual(app.kansui_url('18'),
                         'https://www.kkr.mlit.go.jp/road/doro_bosaijoho_webmap/kml/18_fukui_kansui_point.kml')

    def test_prefs_near(self):
        self.assertEqual(app.prefs_near(35.70, 139.70, 3.0), ['11', '13'])  # 埼玉 is 2.2 km north
        self.assertEqual(app.prefs_near(35.70, 139.70, 1.0), ['13'])
        self.assertEqual(app.prefs_near(26.2, 127.68, 3.0), ['47'])

    def test_radius_order_and_sources(self):
        url13 = app.kansui_url('13')
        self.pages = {url13: KML.encode(), app.kansui_url('11'): None}
        r = app.road_flood(35.70, 139.70, radius_km=3.0)
        self.assertEqual(r['count'], 2)
        self.assertEqual([i['name'] for i in r['items']], ['A&B 交差点', '近いアンダーパス'])
        self.assertLessEqual(r['items'][0]['km'], r['items'][1]['km'])
        self.assertEqual(r['items'][0]['bureau'], '国土交通省 関東地方整備局')
        self.assertEqual(r['sources'], [{'bureau': '国土交通省 関東地方整備局', 'url': url13}])
        self.assertEqual(len(r['errors']), 1)  # 埼玉 answered 404
        self.assertIn('ll=35.700000,139.700000', r['link'])
        self.assertIn('kansui', r['link'])

    def test_okinawa_is_missing_not_an_error(self):
        r = app.road_flood(26.2, 127.68)
        self.assertEqual((r['count'], r['missing_prefs'], r['errors'], self.fetched), (0, ['47'], [], []))

    def test_all_failed_raises(self):
        self.pages = {app.kansui_url('13'): ConnectionResetError('reset')}
        with self.assertRaises(RuntimeError):
            app.road_flood(35.70, 139.70, radius_km=1.0)

    def test_notice_line_only_for_rain_and_flood(self):
        n = app.build_notice(AREA, warn(('03', 'rain', 3)), [], [], None, [], ROAD)
        self.assertIn('道路　冠水想定 7 か所（3km 以内）', n['body'])
        n = app.build_notice(AREA, warn(('49', 'landslide', 4)), [], [], None, [], ROAD)
        self.assertNotIn('冠水', n['body'])
        n = app.build_notice(AREA, warn(('03', 'rain', 3)), [], [], None, [], {**ROAD, 'count': 0, 'items': []})
        self.assertNotIn('冠水', n['body'])


AKITA = app.WILDLIFE_SETS['05']
WILD_CSV = ('\ufeff出没情報ID,情報種別,市町村,地番情報,目撃日時,獣種,性別,単独か親子,頭数,目撃時の状況,x(緯度),y(経度)\n'
            '1,目撃,秋田市,〇〇,2026/09/20 06:10,ツキノワグマ,不明,単独,1,"道を横切った\n山へ入った",39.7010,140.1000\n'
            '2,痕跡(食害),秋田市,〇〇,2026/09/25 17:00,ツキノワグマ,不明,不明,0,柿,39.7100,140.1000\n'
            '3,目撃,秋田市,〇〇,2026/09/25 17:00,ニホンジカ,不明,単独,1,,39.7020,140.1000\n'
            '4,目撃,秋田市,〇〇,2026/09/28 05:00,ツキノワグマ,不明,親子,3,,39.7500,140.1000\n'
            '5,目撃,秋田市,〇〇,2025/08/01 12:00,ツキノワグマ,不明,単独,1,,39.7001,140.1000\n'
            '6,目撃,秋田市,〇〇,日時なし,ツキノワグマ,不明,単独,1,,39.7001,140.1000\n'
            '7,目撃,秋田市,〇〇,2026/09/26 08:00,イノシシ,不明,単独,1,,緯度なし,140.1000\n').encode()


class WildlifeTest(unittest.TestCase):
    NOW = app.datetime(2026, 10, 1, 12, 0, tzinfo=app.JST)

    def setUp(self):
        self.fetched, self.saved = [], (app.fetch, app.fetch_json)
        self.pages, self.pkg = {AKITA['csv']: WILD_CSV}, ConnectionResetError('reset')
        app._wild_parsed.clear()

        def fetch(url, ttl):
            self.fetched.append(url)
            page = self.pages.get(url)
            if isinstance(page, Exception):
                raise page
            return page

        def fetch_json(url, ttl):
            if url == AKITA['api']:
                if isinstance(self.pkg, Exception):
                    raise self.pkg
                return self.pkg
            return RELM
        app.fetch, app.fetch_json = fetch, fetch_json

    def tearDown(self):
        app.fetch, app.fetch_json = self.saved
        app._wild_parsed.clear()

    def test_parse_skips_bad_rows(self):
        rows = app.parse_wildlife(WILD_CSV)
        self.assertEqual(len(rows), 5)  # 日時なし・緯度なし are skipped; the quoted line break stays in one row
        self.assertEqual(rows[0][:2], (39.701, 140.1))
        self.assertEqual(rows[0][3:], ('目撃', 'ツキノワグマ', 1, '秋田市'))
        with self.assertRaises(ValueError):
            app.parse_wildlife('a,b\n1,2\n'.encode())

    def test_radius_days_order_and_counts(self):
        w = app.wildlife(39.70, 140.10, now=self.NOW)
        self.assertTrue(w['covered'])
        self.assertFalse(w['other_prefs'])
        self.assertEqual(w['count'], 3)  # 5.5 km away and 14 months old are left out
        self.assertEqual([i['time'] for i in w['items']], ['2026/09/25 17:00', '2026/09/25 17:00', '2026/09/20 06:10'])
        self.assertEqual([i['animal'] for i in w['items']], ['ニホンジカ', 'ツキノワグマ', 'ツキノワグマ'])  # same time: nearer first
        self.assertEqual(w['by_animal'], {'ツキノワグマ': 2, 'ニホンジカ': 1})
        self.assertEqual(w['by_kind'], {'目撃': 2, '痕跡(食害)': 1})
        self.assertEqual(w['items'][1]['heads'], 0)
        self.assertIn('#16/39.710000/140.100000/', w['items'][1]['link'])
        self.assertEqual(w['sources'][0]['url'], AKITA['csv'])
        self.assertEqual(w['sources'][0]['data_until'], '2026/09/28')
        self.assertEqual(w['errors'], [])

    def test_parse_once_per_download(self):
        app.wildlife(39.70, 140.10, now=self.NOW)
        rows = app._wild_parsed[AKITA['csv']][1]
        app.wildlife(39.71, 140.10, now=self.NOW)
        self.assertIs(app._wild_parsed[AKITA['csv']][1], rows)

    def test_csv_url_from_catalog(self):
        url = 'https://ckan.pref.akita.lg.jp/dataset/x/resource/y/download/new_kumadas.csv'
        self.pkg = {'result': {'title': '県の題', 'resources': [
            {'format': 'CSV', 'url': 'https://elsewhere.example/kumadas.csv'},
            {'format': 'PDF', 'url': 'https://ckan.pref.akita.lg.jp/a.pdf'},
            {'format': 'csv', 'url': url}]}}
        self.pages = {url: WILD_CSV}
        w = app.wildlife(39.70, 140.10, now=self.NOW)
        self.assertEqual((w['sources'][0]['url'], w['sources'][0]['title']), (url, '県の題'))
        self.assertNotIn('https://elsewhere.example/kumadas.csv', self.fetched)

    def test_outside_akita(self):
        w = app.wildlife(35.70, 139.70, now=self.NOW)
        self.assertEqual((w['covered'], w['other_prefs'], w['count'], self.fetched), (False, True, 0, []))

    def test_download_failed_raises(self):
        self.pages = {AKITA['csv']: ConnectionResetError('reset')}
        with self.assertRaises(RuntimeError):
            app.wildlife(39.70, 140.10, now=self.NOW)
        self.pages = {}
        with self.assertRaisesRegex(RuntimeError, '404'):
            app.wildlife(39.70, 140.10, now=self.NOW)

    def bear_notice(self, w=None, lat=39.70):
        wild = app.wildlife(lat, 140.10, now=self.NOW)
        return app.build_notice(AREA, w or {'warnings': [], 'flood': []}, [], [], RAIN, [], ROAD, wild)

    def test_notify_rule(self):
        b = app.wildlife(39.70, 140.10, now=self.NOW)['notify']
        # the bear 1.11 km away, the deer 0.22 km away and the bear 14 months ago are left out
        self.assertEqual((b['count'], b['newest']['time'], b['newest']['km']), (1, '2026/09/20 06:10', 0.11))
        self.assertEqual((b['newest']['short'], b['newest']['data_until']), ('秋田県 クマダス', '2026/09/28'))
        later = app.wildlife(39.70, 140.10, now=self.NOW + app.timedelta(days=61))['notify']
        self.assertEqual((later['count'], later['newest']), (0, None))

    def test_bear_alone_notifies(self):
        n = self.bear_notice()
        self.assertEqual((n['notify'], n['weather']), (True, False))
        self.assertEqual(n['title'], 'ツキノワグマに気をつけて')
        self.assertEqual(n['level'], 0)
        self.assertEqual([f['id'] for f in n['facts']], ['wildlife'])  # no rain or road without a warning
        self.assertEqual(n['body'], 'ツキノワグマ　1km 以内に 1 件（60 日以内）')
        self.assertEqual(n['wildlife']['time'], '2026/09/20 06:10')
        lines = compose.validate(n['facts'], {'lines': [{'text': n['facts'][0]['text'] + '。', 'facts': ['wildlife']}]})
        self.assertEqual(lines, [n['facts'][0]['text'] + '（秋田県 クマダス）。'])

    def test_bear_with_warning(self):
        n = self.bear_notice(warn(('03', 'rain', 3)))
        self.assertEqual(n['title'], '大雨・ツキノワグマに気をつけて')
        self.assertTrue(n['weather'])
        self.assertEqual([f['id'] for f in n['facts']], ['warning', 'rain', 'road', 'wildlife'])

    def test_no_bear_near(self):
        self.assertEqual(self.bear_notice(lat=39.72), {'notify': False})  # nearest bear 1.11 km
        n = self.bear_notice(warn(('03', 'rain', 3)), lat=39.72)
        self.assertNotIn('クマ', n['body'])
        self.assertIsNone(n['wildlife'])

    def test_key_follows_the_record_not_the_distance(self):
        a, moved = self.bear_notice(), self.bear_notice(lat=39.7003)
        self.assertNotEqual(a['facts'][0]['text'], moved['facts'][0]['text'])  # 0.11 km → 0.08 km
        self.assertEqual(a['key'], moved['key'])
        self.pages = {AKITA['csv']: WILD_CSV + '8,目撃,秋田市,〇〇,2026/09/30 07:00,ツキノワグマ,不明,単独,1,,39.7005,140.1000\n'.encode()}
        app._wild_parsed.clear()
        newer = self.bear_notice()
        self.assertNotEqual(a['key'], newer['key'])
        self.assertIn('2 件', newer['body'])


class DemoTest(unittest.TestCase):
    NOW = app.datetime(2026, 11, 3, 23, 30, 5, tzinfo=app.JST)

    def test_parse(self):
        self.assertEqual(app.parse_demo('landslide:5'), ('landslide', 5))
        self.assertEqual(app.parse_demo('tide'), ('tide', 4))
        for bad in ('wind:3', 'rain:9', 'rain:x', '', 'flood:1'):
            with self.assertRaises(ValueError):
                app.parse_demo(bad)

    def test_every_choice_has_a_code(self):
        for h in app.GROUP1:
            for lv in app.DEMO_LEVELS:
                w = app.demo_warning(h, lv, self.NOW)
                table = app.FLOOD_CODES if h == 'flood' else {c: v[1] for c, v in app.WARNING_CODES.items()}
                self.assertEqual(table[w['code']], lv)
                self.assertIn('試しの作りもの', w['name'])

    def test_fake_alone_is_marked_and_not_called_official(self):
        w = app.add_demo({'warnings': [], 'flood': []}, ('landslide', 4), self.NOW)
        n = app.build_notice(AREA, w, ZONE_ITEMS, KIKI, RAIN, LORE)
        self.assertTrue(n['notify'])
        self.assertEqual(n['title'], '土砂災害に気をつけて（試し）')  # 試し where the lock screen shows it
        lines = n['body'].split('\n')
        self.assertTrue(lines[0].startswith('ここ　'))
        self.assertEqual(lines[-1], '警報は試しの作りもの。ほかは本物')
        self.assertEqual(n['notes'], lines[-1:])
        self.assertNotIn('警報　', n['body'])  # the fake one is never shown as a 気象庁 fact
        self.assertIn('ここ　土砂キキクル', n['body'])
        self.assertIn('試しの作りもの', n['facts'][0]['text'])  # Claude and the screen get the full note

    def test_fake_with_real(self):
        w = app.add_demo(warn(('03', 'rain', 3)), ('tide', 5), self.NOW)
        n = app.build_notice(AREA, w, ZONE_ITEMS, KIKI, RAIN, LORE)
        self.assertTrue(n['body'].endswith('警報　レベル３大雨警報\n警報は試しの作りもの。ほかは本物'))
        self.assertNotIn('高潮特別警報', n['body'])  # the fake one is only in the title
        self.assertEqual(n['title'], '高潮（試し）・大雨に気をつけて')
        self.assertEqual(w['warnings'][0]['hazard'], 'tide')

    def test_flood_goes_with_rivers_and_key_changes(self):
        w = app.add_demo({'warnings': [], 'flood': []}, ('flood', 3), self.NOW)
        self.assertEqual(len(w['flood']), 1)
        a = app.build_notice(AREA, w, [], [], None, [])['key']
        later = app.add_demo({'warnings': [], 'flood': []}, ('flood', 3), self.NOW + app.timedelta(seconds=1))
        self.assertNotEqual(a, app.build_notice(AREA, later, [], [], None, [])['key'])

    def test_level2_fake_does_not_notify(self):
        w = app.add_demo({'warnings': [], 'flood': []}, ('rain', 2), self.NOW)
        self.assertFalse(app.build_notice(AREA, w, ZONE_ITEMS, KIKI, RAIN, LORE)['notify'])


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
        self.assertEqual(self.get('/api/here?lat=35.68&lon=139.75&demo=wind:3')[0], 400)

    def test_other_host_refused(self):
        self.assertEqual(self.get('/', host='example.com')[0], 403)


if __name__ == '__main__':
    unittest.main()
