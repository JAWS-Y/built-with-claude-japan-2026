"""いまいる場所の公式情報（試作 1: 気象の部分）。

端末の現在地から、その場所の公式情報（気象庁・国土交通省・国土地理院）を読み、
レベル３以上の大雨・土砂災害・高潮・氾濫の情報が出ていれば、その場所の性質と一緒に知らせる。
予報や「どう動くか」は自分では作らない。公式の情報をそのまま、出典とリンクを付けて渡す。

標準ライブラリだけで動く。待ち受けは 127.0.0.1 だけ。
  python3 app.py                         → http://127.0.0.1:8765/ を開く
  python3 app.py --point 35.71,139.82    → その地点の結果を JSON で表示
"""
import argparse
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from png import Png

HERE = os.path.dirname(os.path.abspath(__file__))
JMA = 'https://www.jma.go.jp'
GSI_RASTER = 'https://disaportaldata.gsi.go.jp/raster'
GSI_LORE = 'https://cyberjapandata.gsi.go.jp/xyz/disaster_lore_all'
UA = 'hackathon-prototype (Built with Claude Japan 2026; local test)'
DAY, MINUTE = 86400, 60
JST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- fetch

_cache, _cache_lock = {}, threading.Lock()


def fetch(url, ttl):
    """Bytes of url, or None on 404. Answers (404 too) are kept for ttl seconds."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            data = res.read()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        data = None
    with _cache_lock:
        if len(_cache) > 3000:
            for k in [k for k, (t, _) in _cache.items() if now - t > MINUTE]:
                del _cache[k]
        _cache[url] = (now, data)
    return data


def fetch_json(url, ttl):
    data = fetch(url, ttl)
    return None if data is None else json.loads(data)


# ---------------------------------------------------------------- geometry


def tile_px(lat, lon, z):
    """Web mercator tile (tx, ty) and pixel (px, py) in a 256 px tile."""
    n = 2 ** z
    x = (lon + 180) / 360 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    tx, ty = int(x), int(y)
    return tx, ty, min(255, int((x - tx) * 256)), min(255, int((y - ty) * 256))


def pixel(url_tmpl, lat, lon, z, ttl):
    """RGBA at the point, or None when the tile does not exist (404 or empty)."""
    tx, ty, px, py = tile_px(lat, lon, z)
    data = fetch(url_tmpl.format(z=z, x=tx, y=ty), ttl)
    if not data:
        return None
    return Png(data).rgba(px, py)


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _in_ring(ring, x, y):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_polygon(rings, x, y):
    return bool(rings) and _in_ring(rings[0], x, y) and not any(_in_ring(h, x, y) for h in rings[1:])


def in_geometry(geom, x, y):
    kind = geom.get('type')
    if kind == 'Polygon':
        return _in_polygon(geom['coordinates'], x, y)
    if kind == 'MultiPolygon':
        return any(_in_polygon(p, x, y) for p in geom['coordinates'])
    if kind == 'GeometryCollection':
        return any(in_geometry(g, x, y) for g in geom.get('geometries', []))
    return False


def in_features(gj, lon, lat):
    feats = gj.get('features', []) if gj.get('type') == 'FeatureCollection' else [gj]
    return any(f.get('geometry') and in_geometry(f['geometry'], lon, lat) for f in feats)


def nearest(rgba, table, max_dist=None):
    """Legend value whose colour is nearest to rgba; None for transparent or too far."""
    if rgba is None or rgba[3] < 128:
        return None
    best, best_d = None, None
    for color, value in table:
        d = math.dist(rgba[:3], color)
        if best_d is None or d < best_d:
            best, best_d = value, d
    if max_dist is not None and best_d > max_dist:
        return None
    return best


def jst_stamp(stamp):
    """JMA tile time (UTC, YYYYmmddHHMMSS) as JST text."""
    t = datetime.strptime(stamp, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    return t.astimezone(JST).strftime('%m/%d %H:%M')


def jst_iso(text):
    try:
        return datetime.fromisoformat(text).astimezone(JST).strftime('%m/%d %H:%M')
    except (TypeError, ValueError):
        return text or ''


# ---------------------------------------------------------------- area (気象庁の市町村等の区分)


def find_area(lat, lon):
    relm = fetch_json(JMA + '/bosai/common/const/class20relm.json', DAY)
    cands = [(c, v) for c, v in relm.items()
             if v['sw'][0] <= lat <= v['ne'][0] and v['sw'][1] <= lon <= v['ne'][1]]
    cands.sort(key=lambda cv: (cv[1]['ne'][0] - cv[1]['sw'][0]) * (cv[1]['ne'][1] - cv[1]['sw'][1]))
    code, method = None, None
    for c, _ in cands:
        gj = fetch_json(JMA + f'/bosai/common/const/geojson/class20s/{c}.json', DAY)
        if gj and in_features(gj, lon, lat):
            code, method = c, 'polygon'
            break
    if code is None and cands:
        code, method = cands[0][0], 'bbox'
    if code is None:
        def centre_km(c):
            v = relm[c]
            return km(lat, lon, (v['ne'][0] + v['sw'][0]) / 2, (v['ne'][1] + v['sw'][1]) / 2)
        code, method = min(relm, key=centre_km), 'nearest'
    area = fetch_json(JMA + '/bosai/common/const/area.json', DAY)
    c15 = area['class20s'][code]['parent']
    c10 = area['class15s'][c15]['parent']
    office = area['class10s'][c10]['parent']
    return {
        'method': method,
        'class20': {'code': code, 'name': area['class20s'][code]['name']},
        'class15': {'code': c15, 'name': area['class15s'][c15]['name']},
        'class10': {'code': c10, 'name': area['class10s'][c10]['name']},
        'office': {'code': office, 'name': area['offices'][office]['name'],
                   'officeName': area['offices'][office].get('officeName', '')},
    }


# ---------------------------------------------------------------- warnings (警報・注意報、氾濫の情報)

# 気象庁の警報ページの表（bosai/warning）と同じ対応
WARNING_CODES = {
    '33': ('rain', 5), '43': ('rain', 4), '03': ('rain', 3), '10': ('rain', 2),
    '39': ('landslide', 5), '49': ('landslide', 4), '09': ('landslide', 3), '29': ('landslide', 2),
    '38': ('tide', 5), '48': ('tide', 4), '08': ('tide', 3), '19': ('tide', 2),
    '35': ('wind', 5), '05': ('wind', 3), '15': ('wind', 2),
    '32': ('wind_snow', 5), '02': ('wind_snow', 3), '13': ('wind_snow', 2),
    '36': ('snow', 5), '06': ('snow', 3), '12': ('snow', 2),
    '37': ('wave', 5), '07': ('wave', 3), '16': ('wave', 2),
    '14': ('thunder', 2), '17': ('snow_melting', 2), '20': ('fog', 2), '21': ('dry', 2),
    '22': ('avalanche', 2), '23': ('cold', 2), '24': ('frost', 2), '25': ('ice_accretion', 2),
    '26': ('snow_accretion', 2),
}
FLOOD_CODES = {'53': 5, '51': 5, '41': 4, '40': 4, '31': 3, '30': 3, '22': 2, '21': 2, '20': 2}
LEVELED = {'rain': '大雨', 'landslide': '土砂災害', 'flood': '氾濫', 'tide': '高潮'}
UNLEVELED = {'wind': ('強風', '暴風', '暴風'), 'wind_snow': ('風雪', '暴風雪', '暴風雪'),
             'snow': ('大雪', '大雪', '大雪'), 'wave': ('波浪', '波浪', '波浪')}
ADVISORY_ONLY = {'thunder': '雷', 'snow_melting': '融雪', 'fog': '濃霧', 'dry': '乾燥', 'avalanche': 'なだれ',
                 'cold': '低温', 'frost': '霜', 'ice_accretion': '着氷', 'snow_accretion': '着雪'}
RANK = {2: '注意報', 3: '警報', 4: '危険警報', 5: '特別警報'}
ZEN = {2: '２', 3: '３', 4: '４', 5: '５'}
GROUP1 = ('rain', 'landslide', 'tide', 'flood')


def warning_name(hazard, level):
    if hazard in LEVELED:
        return f'レベル{ZEN[level]}{LEVELED[hazard]}{RANK[level]}'
    if hazard in UNLEVELED:
        return UNLEVELED[hazard][{2: 0, 3: 1, 5: 2}[level]] + RANK[level]
    return ADVISORY_ONLY[hazard] + '注意報'


def is_active(status):
    return bool(status) and '解除' not in status and 'なし' not in status


def _area_kinds(report, c20, c10):
    w = report.get('warning') or {}
    for key, code in (('class20Items', c20), ('class10Items', c10)):
        for item in w.get(key) or []:
            if item.get('areaCode') == code:
                return item.get('kinds') or []
    return []


def decode_warnings(reports, c20, c10):
    """Active warnings for the area. For each code the newest report wins."""
    latest, unknown = {}, set()
    for rep in reports or []:
        t = rep.get('reportDatetime') or ''
        for k in _area_kinds(rep, c20, c10):
            code = k.get('code')
            if code is None:
                continue
            if code not in WARNING_CODES:
                if is_active(k.get('status', '')):
                    unknown.add(code)
                continue
            if code not in latest or t > latest[code]['reportDatetime']:
                latest[code] = {'status': k.get('status', ''), 'reportDatetime': t}
    out = []
    for code, v in latest.items():
        if not is_active(v['status']):
            continue
        hazard, level = WARNING_CODES[code]
        out.append({'code': code, 'hazard': hazard, 'level': level, 'name': warning_name(hazard, level),
                    'status': v['status'], 'reportDatetime': v['reportDatetime'],
                    'time': jst_iso(v['reportDatetime']), 'group1': hazard in GROUP1})
    out.sort(key=lambda w: (-w['level'], w['code']))
    return out, sorted(unknown)


def decode_flood(entries, c20, c10):
    """Active 氾濫 information (designated rivers) that names the area."""
    out = []
    for e in entries or []:
        item = e.get('item') or {}
        if c20 not in (e.get('class20Codes') or []) and c10 not in (e.get('class10Codes') or []):
            continue
        code = str(item.get('code', ''))
        if code not in FLOOD_CODES or not is_active(item.get('status', '')):
            continue
        level = FLOOD_CODES[code]
        detail = {k: v for k, v in item.items() if isinstance(v, str) and k not in ('code', 'status')}
        out.append({'code': code, 'hazard': 'flood', 'level': level, 'name': warning_name('flood', level),
                    'status': item.get('status', ''), 'reportDatetime': e.get('reportDatetime', ''),
                    'time': jst_iso(e.get('reportDatetime', '')), 'detail': detail, 'group1': True})
    out.sort(key=lambda w: -w['level'])
    return out


def warnings_for(area):
    c20, c10, office = area['class20']['code'], area['class10']['code'], area['office']['code']
    reports = fetch_json(JMA + f'/bosai/warning/data/r8/{office}.json', MINUTE)
    warnings, unknown = decode_warnings(reports, c20, c10)
    flood = decode_flood(fetch_json(JMA + '/bosai/flood/data/r8/flood_xml.json', MINUTE), c20, c10)
    return {'warnings': warnings, 'flood': flood, 'unknown_codes': unknown,
            'link': JMA + f'/bosai/warning/#area_type=class20s&area_code={c20}&lang=ja',
            'flood_link': JMA + '/bosai/flood/'}


# ---------------------------------------------------------------- キキクル・降水ナウキャスト・アメダス

RISK_LEGEND = [((12, 0, 12), (5, '災害切迫（黒）警戒レベル５相当')), ((170, 0, 170), (4, '危険（紫）警戒レベル４相当')),
               ((255, 40, 0), (3, '警戒（赤）警戒レベル３相当')), ((242, 231, 0), (2, '注意（黄）警戒レベル２相当')),
               ((255, 255, 255), (0, '今後の情報等に留意'))]
KIKIKURU = [('land', '土砂キキクル（土砂災害の危険度分布）'), ('inund', '浸水キキクル（大雨による浸水の危険度分布）')]
NOWC_LEGEND = [((180, 0, 104), '80以上'), ((255, 40, 0), '50～80'), ((255, 153, 0), '30～50'),
               ((255, 245, 0), '20～30'), ((250, 245, 0), '20～30'), ((0, 65, 255), '10～20'),
               ((33, 140, 255), '5～10'), ((160, 210, 255), '1～5'), ((242, 242, 255), '1未満')]


def map_link(page, lat, lon, element):
    return JMA + f'/bosai/{page}/#zoom:13/lat:{lat:.6f}/lon:{lon:.6f}/colordepth:normal/elements:{element}'


def kikikuru(lat, lon):
    times = fetch_json(JMA + '/bosai/jmatile/data/risk/targetTimes.json', MINUTE)
    out = []
    for element, label in KIKIKURU:
        ents = [e for e in times if element in e.get('elements', [])]
        base = max(e['basetime'] for e in ents)
        e = min((e for e in ents if e['basetime'] == base), key=lambda e: e['validtime'])
        url = JMA + (f"/bosai/jmatile/data/risk/{e['basetime']}/{e.get('member', 'none')}/{e['validtime']}"
                     f"/surf/{element}/{{z}}/{{x}}/{{y}}.png")
        rgba = pixel(url, lat, lon, 10, MINUTE)
        if rgba is None:
            level, meaning = None, 'この場所の画像がありません'
        elif rgba[3] < 128:
            level, meaning = 0, '今後の情報等に留意'
        else:
            level, meaning = nearest(rgba, RISK_LEGEND, 60) or (None, f'凡例にない色 {rgba[:3]}')
        out.append({'key': element, 'label': label, 'level': level, 'meaning': meaning,
                    'time': jst_stamp(e['validtime']), 'link': map_link('risk', lat, lon, element)})
    return out


def _rain_at(url, lat, lon):
    rgba = pixel(url, lat, lon, 10, MINUTE)
    if rgba is None:
        return 'この場所の画像がありません'
    if rgba[3] < 128:
        return '降っていない'
    band = nearest(rgba, NOWC_LEGEND, 60)
    return f'{band} mm/h' if band else f'凡例にない色 {rgba[:3]}'


def nowcast(lat, lon):
    n1 = fetch_json(JMA + '/bosai/jmatile/data/nowc/targetTimes_N1.json', MINUTE)
    n2 = fetch_json(JMA + '/bosai/jmatile/data/nowc/targetTimes_N2.json', MINUTE)
    # N1 is sometimes updated a little before N2: use the newest time that both lists have
    bases = {e['basetime'] for e in n1 if e['basetime'] == e['validtime']}
    common = bases & {e['basetime'] for e in n2}
    base = max(common or bases)
    t0 = datetime.strptime(base, '%Y%m%d%H%M%S')
    tmpl = JMA + '/bosai/jmatile/data/nowc/{b}/none/{v}/surf/hrpns/{{z}}/{{x}}/{{y}}.png'
    out = {'now': {'time': jst_stamp(base), 'rain': _rain_at(tmpl.format(b=base, v=base), lat, lon)},
           'link': map_link('nowc', lat, lon, 'hrpns')}
    for key, minutes in (('plus30', 30), ('plus60', 60)):
        v = (t0 + timedelta(minutes=minutes)).strftime('%Y%m%d%H%M%S')
        if any(e['basetime'] == base and e['validtime'] == v for e in n2):
            out[key] = {'time': jst_stamp(v), 'rain': _rain_at(tmpl.format(b=base, v=v), lat, lon)}
    return out


def amedas(lat, lon):
    latest = fetch(JMA + '/bosai/amedas/data/latest_time.txt', MINUTE).decode().strip()
    stamp = datetime.fromisoformat(latest).astimezone(JST).strftime('%Y%m%d%H%M%S')
    obs_all = fetch_json(JMA + f'/bosai/amedas/data/map/{stamp}.json', MINUTE)
    table = fetch_json(JMA + '/bosai/amedas/const/amedastable.json', DAY)
    best = None
    for sid, st in table.items():
        obs = obs_all.get(sid)
        if not obs or 'precipitation1h' not in obs:
            continue
        d = km(lat, lon, st['lat'][0] + st['lat'][1] / 60, st['lon'][0] + st['lon'][1] / 60)
        if best is None or d < best[0]:
            best = (d, sid, st, obs)
    if best is None:
        return None
    d, sid, st, obs = best

    def val(key):
        v = obs.get(key)
        return None if not v or v[0] is None else v[0]
    return {'station': st['kjName'], 'kana': st.get('knName', ''), 'id': sid, 'km': round(d, 1),
            'time': jst_iso(latest), 'precipitation10m': val('precipitation10m'),
            'precipitation1h': val('precipitation1h'), 'precipitation3h': val('precipitation3h'),
            'precipitation24h': val('precipitation24h'),
            'link': JMA + f'/bosai/amedas/#amdno={sid}'}


# ---------------------------------------------------------------- 場所の性質（ハザードマップ）

DEPTH = [((220, 122, 220), '20m以上'), ((242, 133, 201), '10～20m'), ((255, 145, 145), '5～10m'),
         ((255, 183, 183), '3～5m'), ((255, 216, 192), '0.5～3m'), ((248, 225, 166), '0.5～1m'),
         ((247, 245, 169), '0.5m未満'), ((255, 255, 179), '0.3m未満')]
DEPTH_NAISUI = [((220, 122, 220), '20m以上'), ((242, 133, 201), '10～20m'), ((255, 145, 145), '5～10m'),
                ((255, 183, 183), '3～5m'), ((255, 216, 192), '0.5～3m'), ((248, 225, 166), '0.5～1m'),
                ((247, 245, 169), '0.1～0.5m'), ((255, 255, 179), '0.1～0.3m')]
ZONES = [
    ('flood', '洪水浸水想定区域（想定最大規模）', '01_flood_l2_shinsuishin_data', 'depth'),
    ('kaoku_hanran', '家屋倒壊等氾濫想定区域（氾濫流）', '01_flood_l2_kaokutoukai_hanran_data', 'presence'),
    ('kaoku_kagan', '家屋倒壊等氾濫想定区域（河岸侵食）', '01_flood_l2_kaokutoukai_kagan_data', 'presence'),
    ('naisui', '内水浸水想定区域', '02_naisui_data', 'depth_naisui'),
    ('hightide', '高潮浸水想定区域', '03_hightide_l2_shinsuishin_data', 'depth'),
    ('tsunami', '津波浸水想定', '04_tsunami_newlegend_data', 'presence'),
    ('dosekiryu', '土砂災害警戒区域（土石流）', '05_dosekiryukeikaikuiki', 'sediment'),
    ('kyukeisha', '土砂災害警戒区域（急傾斜地の崩壊）', '05_kyukeishakeikaikuiki', 'sediment'),
    ('jisuberi', '土砂災害警戒区域（地すべり）', '05_jisuberikeikaikuiki', 'sediment'),
    ('nadare', '雪崩危険箇所', '05_nadarekikenkasyo', 'presence'),
]


def zone_value(mode, rgba):
    """(in_zone, value text) from one pixel of a hazard map tile."""
    if rgba is None or rgba[3] < 128:
        return False, None
    if mode == 'depth':
        return True, nearest(rgba, DEPTH, 40) or f'凡例にない色 {rgba[:3]}'
    if mode == 'depth_naisui':
        return True, nearest(rgba, DEPTH_NAISUI, 40) or f'凡例にない色 {rgba[:3]}'
    if mode == 'sediment':
        return True, '特別警戒区域' if rgba[1] < 100 else '警戒区域'
    return True, None


def zones(lat, lon):
    def one(z):
        key, label, stem, mode = z
        entry = {'key': key, 'label': label}
        try:
            entry['in'], entry['value'] = zone_value(mode, pixel(GSI_RASTER + f'/{stem}/{{z}}/{{x}}/{{y}}.png',
                                                                 lat, lon, 17, DAY))
        except Exception as e:  # one layer failing must not hide the others
            entry['in'], entry['value'], entry['error'] = None, None, f'{type(e).__name__}: {e}'
        return entry
    with ThreadPoolExecutor(len(ZONES)) as ex:
        out = list(ex.map(one, ZONES))
    return {'items': out, 'link': f'https://disaportal.gsi.go.jp/maps/?ll={lat:.6f},{lon:.6f}&z=16&base=pale'}


# ---------------------------------------------------------------- 自然災害伝承碑


def _plain(text):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text or '')).strip()


def lore(lat, lon, radius_km=10.0, limit=5):
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    tiles = {tile_px(a, b, 7)[:2] for a in (lat - dlat, lat + dlat) for b in (lon - dlon, lon + dlon)}
    found = []
    for tx, ty in sorted(tiles):
        gj = fetch_json(GSI_LORE + f'/7/{tx}/{ty}.geojson', DAY)
        for f in (gj or {}).get('features', []):
            g = f.get('geometry') or {}
            if g.get('type') != 'Point':
                continue
            plon, plat = g['coordinates'][:2]
            d = km(lat, lon, plat, plon)
            if d > radius_km:
                continue
            p = f.get('properties') or {}
            image = p.get('Image') or ''
            found.append({'km': round(d, 2), 'name': _plain(p.get('LoreName')), 'disaster': _plain(p.get('DisasterName')),
                          'kind': _plain(p.get('DisasterKind')), 'year': _plain(str(p.get('LoreYear') or '')),
                          'address': _plain(p.get('Address')), 'info': _plain(p.get('DisasterInfo')),
                          'image': image if image.startswith('https://') else '',
                          'link': f'https://maps.gsi.go.jp/#15/{plat:.6f}/{plon:.6f}/&base=std&ls=std%7Cdisaster_lore_all'})
    found.sort(key=lambda x: x['km'])
    return {'radius_km': radius_km, 'count': len(found), 'items': found[:limit]}


# ---------------------------------------------------------------- 知らせ

LINKS = {
    'rain': {'zones': ['naisui'], 'kiki': ['inund'], 'lore': ['洪水']},
    'landslide': {'zones': ['dosekiryu', 'kyukeisha', 'jisuberi'], 'kiki': ['land'], 'lore': ['土砂災害']},
    'flood': {'zones': ['flood', 'kaoku_hanran', 'kaoku_kagan'], 'kiki': [], 'lore': ['洪水']},
    'tide': {'zones': ['hightide'], 'kiki': [], 'lore': ['高潮']},
}


def build_notice(area, warn, zone_items, kiki, rain, lore_items):
    """Notify when a レベル３以上 大雨・土砂災害・高潮・氾濫 is out. The text lists official facts only."""
    trig = [w for w in (warn or {}).get('warnings', []) + (warn or {}).get('flood', [])
            if w['group1'] and w['level'] >= 3]
    if not trig:
        return {'notify': False}
    hazards = {w['hazard'] for w in trig}
    zone_keys = {k for h in hazards for k in LINKS[h]['zones']}
    kiki_keys = {k for h in hazards for k in LINKS[h]['kiki']}
    lore_kinds = {k for h in hazards for k in LINKS[h]['lore']}
    names = '、'.join(dict.fromkeys(w['name'] for w in trig))
    real = '、'.join(dict.fromkeys(w['name'] for w in trig if not w.get('demo')))
    fake = '、'.join(dict.fromkeys(warning_name(w['hazard'], w['level']) for w in trig if w.get('demo')))
    lines = []
    if fake:
        lines.append(f'これは試しです。{fake}は作りもので、気象庁は出していません。ほかはいまの本物の情報です。')
    if real:
        lines.append(f"{area['class20']['name']}に{real}が出ています（気象庁）。")
    place = []
    for z in zone_items or []:
        if z['key'] in zone_keys and z.get('in'):
            place.append(f"{z['label']}" + (f"（{z['value']}）" if z.get('value') else ''))
    if place:
        lines.append('いる場所は ' + '、'.join(place) + ' に入っています（ハザードマップポータル）。')
    for k in kiki or []:
        if k['key'] in kiki_keys and k.get('level') is not None:
            lines.append(f"{k['label'].split('（')[0]}はこの地点で「{k['meaning']}」です（気象庁 {k['time']}）。")
    if rain and 'now' in rain:
        text = f"降水ナウキャストではこの地点 いま {rain['now']['rain']}"
        if 'plus60' in rain:
            text += f"、60分後 {rain['plus60']['rain']}"
        lines.append(text + '（気象庁）。')
    for item in lore_items or []:
        if any(kind in item['kind'] for kind in lore_kinds):
            lines.append(f"{item['km']}km 先に{item['kind']}の伝承碑「{item['name']}」があります（国土地理院）。")
            break
    key = area['class20']['code'] + '|' + ','.join(sorted(f"{w['code']}@{w['reportDatetime']}" for w in trig))
    title = ('【試し】' if fake else '') + f"{area['class20']['name']}: {names}"
    return {'notify': True, 'title': title, 'body': '\n'.join(lines),
            'key': key, 'place_found': bool(place), 'places': [z['key'] for z in zone_items or []
                                                               if z['key'] in zone_keys and z.get('in')]}


SOURCES = [
    {'name': '気象庁ホームページ', 'url': 'https://www.jma.go.jp/',
     'note': '出典：気象庁ホームページ（警報・注意報、氾濫の情報、キキクル、降水ナウキャスト、アメダス）。'
             'キキクルと降水ナウキャストは、地図画像の画素の色を読み取って文字にしています。'},
    {'name': 'ハザードマップポータルサイト（国土交通省・国土地理院）', 'url': 'https://disaportal.gsi.go.jp/',
     'note': 'データは国土交通省・都道府県・市町村等。地図画像の画素の色を読み取って文字にしています。'},
    {'name': '国土地理院 自然災害伝承碑', 'url': 'https://www.gsi.go.jp/bousaichiri/denshouhi.html',
     'note': '出典：国土地理院 自然災害伝承碑。'},
]
NOT_INCLUDED = [
    '洪水キキクル（川の線のデータ）: 今は日本全体どのタイルも空で、中身の形を確かめられていないため入れていません。',
    '道路冠水の情報（自治体ごとの公開データ）: 次に入れます。',
    '浸水継続時間・津波の深さ: 凡例をまだ読み取っていないため、津波は「区域に入るか」だけです。',
    'Claude による文の組み立て: この試作では入れていません（知らせの文は公式の事実を並べるだけ）。',
]


def in_japan(lat, lon):
    return 20.0 <= lat <= 46.5 and 122.0 <= lon <= 154.0


# ---------------------------------------------------------------- 試し（作りものの警報）

DEMO_LEVELS = (2, 3, 4, 5)


def parse_demo(text):
    """'landslide:4' -> ('landslide', 4). The level defaults to 4. ValueError for anything else."""
    hazard, _, level = text.partition(':')
    level = int(level) if level else 4
    if hazard not in GROUP1 or level not in DEMO_LEVELS:
        raise ValueError(text)
    return hazard, level


def demo_warning(hazard, level, now):
    """A made-up warning to try the notice with. Its name, status and the notice say it is made up."""
    codes = FLOOD_CODES if hazard == 'flood' else {c: lv for c, (h, lv) in WARNING_CODES.items() if h == hazard}
    code = next(c for c, lv in codes.items() if lv == level)
    w = {'code': code, 'hazard': hazard, 'level': level, 'name': warning_name(hazard, level) + '（試しの作りもの）',
         'status': '試し', 'reportDatetime': now.isoformat(timespec='seconds'), 'time': now.strftime('%m/%d %H:%M'),
         'group1': True, 'demo': True}
    if hazard == 'flood':
        w['detail'] = {}
    return w


def add_demo(warn, demo, now):
    """warn with the made-up warning put first in its list (氾濫 goes with the river information)."""
    w = demo_warning(*demo, now)
    part = 'flood' if w['hazard'] == 'flood' else 'warnings'
    return {**warn, part: [w] + warn[part]}


def here(lat, lon, demo=None):
    now = datetime.now(JST)
    out = {'point': {'lat': lat, 'lon': lon}, 'checked_at': now.strftime('%Y-%m-%d %H:%M'),
           'errors': [], 'sources': SOURCES, 'not_included': NOT_INCLUDED}

    def run(part, fn, *args):
        try:
            return fn(*args)
        except Exception as e:
            out['errors'].append({'part': part, 'error': f'{type(e).__name__}: {e}'})
            return None
    with ThreadPoolExecutor(6) as ex:
        jobs = {'kikikuru': ex.submit(run, 'キキクル', kikikuru, lat, lon),
                'nowcast': ex.submit(run, '降水ナウキャスト', nowcast, lat, lon),
                'amedas': ex.submit(run, 'アメダス', amedas, lat, lon),
                'zones': ex.submit(run, 'ハザードマップ', zones, lat, lon),
                'lore': ex.submit(run, '自然災害伝承碑', lore, lat, lon)}
        area = run('区域', find_area, lat, lon)
        warn = run('警報・注意報', warnings_for, area) if area else None
        for k, f in jobs.items():
            out[k] = f.result()
    if demo and warn:
        warn = add_demo(warn, demo, now)
    out['demo'] = {'hazard': demo[0], 'level': demo[1]} if demo else None
    out['area'], out['warning'] = area, warn
    out['notice'] = build_notice(area, warn, (out['zones'] or {}).get('items'), out['kikikuru'], out['nowcast'],
                                 (out['lore'] or {}).get('items')) if area else {'notify': False}
    return out


# ---------------------------------------------------------------- server (127.0.0.1 only)


class Handler(BaseHTTPRequestHandler):
    server_version = 'hackathon-prototype'

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                                    "style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def do_GET(self):
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0]
        if host not in ('127.0.0.1', 'localhost'):
            return self._json(403, {'error': 'host'})
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/':
            with open(os.path.join(HERE, 'index.html'), 'rb') as f:
                return self._send(200, f.read(), 'text/html; charset=utf-8')
        if url.path == '/api/here':
            q = urllib.parse.parse_qs(url.query)
            try:
                lat, lon = float(q['lat'][0]), float(q['lon'][0])
            except (KeyError, ValueError):
                return self._json(400, {'error': 'lat と lon を数で渡してください'})
            if not in_japan(lat, lon):
                return self._json(400, {'error': '日本の範囲の外です'})
            demo = None
            if q.get('demo', [''])[0]:
                try:
                    demo = parse_demo(q['demo'][0])
                except ValueError:
                    return self._json(400, {'error': 'demo は rain・landslide・flood・tide と :2～5 です（例 landslide:4）'})
            return self._json(200, here(lat, lon, demo))
        return self._json(404, {'error': 'not found'})

    def log_message(self, fmt, *args):  # the position is in the query; do not log it
        sys.stderr.write('%s %s\n' % (self.command, urllib.parse.urlsplit(self.path).path))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--point', help='lat,lon を 1 回だけ調べて JSON を表示')
    ap.add_argument('--demo', type=parse_demo, metavar='HAZARD[:LEVEL]',
                    help='--point と一緒に使う。作りものの警報を足して知らせを試す（例 landslide:4）')
    ap.add_argument('--port', type=int, default=8765)
    a = ap.parse_args()
    if a.point:
        lat, lon = (float(v) for v in a.point.split(','))
        if not in_japan(lat, lon):
            sys.exit('日本の範囲の外です')
        print(json.dumps(here(lat, lon, a.demo), ensure_ascii=False, indent=1))
        return
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    print(f'http://127.0.0.1:{a.port}/ を開いてください（Ctrl+C で止まります）')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
