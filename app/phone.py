"""携帯に知らせを送る（Firebase 経由、試作）。

携帯のアプリが自分の位置を Firestore に書き、この PC がそれを読みに行って調べ、
知らせることがあれば Firebase Cloud Messaging の優先度の高い push で携帯に送る。
PC は読みに行くだけで、外からの接続は受けない。位置は画面にも記録にも出さない。

  python3 phone.py pair --google-services google-services.json   携帯に貼る設定の文を出す
  python3 phone.py watch --key service-account.json              数分ごとに調べ、新しい知らせだけ送る
  python3 phone.py test --key service-account.json               作りものの警報でいま 1 回送る
  python3 phone.py list | forget NAME --key service-account.json

標準ライブラリだけで動く（サービスアカウントの署名 RS256 もここで計算する）。
"""
import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import app
from app import JST, UA, here, in_japan, parse_demo

PACKAGE = 'io.github.jawsy.imakoko'
STATE = os.path.join(app.HERE, '.phone', 'phone.json')
SCOPES = ['https://www.googleapis.com/auth/datastore', 'https://www.googleapis.com/auth/firebase.messaging']
FIRESTORE = 'https://firestore.googleapis.com/v1'
FCM = 'https://fcm.googleapis.com/v1'
DATA_LIMIT = 3800  # FCM data messages carry up to 4096 bytes
OLD_POSITION = 2 * 3600
PHONE_LINES = 3  # official pages named on the phone's notice, before the notes


# ---------------------------------------------------------------- RS256（標準ライブラリだけで）

def _der(buf, i):
    """(tag, value, next index) of the DER item at i."""
    tag, n = buf[i], buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], 'big')
        i += k
    return tag, buf[i:i + n], i + n


def _items(body):
    out, i = [], 0
    while i < len(body):
        tag, value, i = _der(body, i)
        out.append((tag, value))
    return out


RSA_OID = bytes.fromhex('2a864886f70d010101')  # 1.2.840.113549.1.1.1 rsaEncryption


def rsa_key(pem):
    """(n, d) of a PEM RSA private key: PKCS#8 'PRIVATE KEY' (service-account keys) or PKCS#1."""
    m = re.search(r'-----BEGIN (RSA )?PRIVATE KEY-----(.*?)-----END', pem, re.S)
    if not m:
        raise ValueError('RSA の秘密鍵が見つかりません')
    _, body, _ = _der(base64.b64decode(''.join(m.group(2).split())), 0)
    items = _items(body)
    if not m.group(1):
        if RSA_OID not in items[1][1]:
            raise ValueError('RSA の鍵ではありません')
        _, body, _ = _der(items[2][1], 0)
        items = _items(body)
    ints = [int.from_bytes(v, 'big') for _, v in items]  # version, n, e, d, p, q, ...
    return ints[1], ints[3]


SHA256_INFO = bytes.fromhex('3031300d060960864801650304020105000420')


def rs256(pem, data):
    """RSASSA-PKCS1-v1_5 with SHA-256 (RFC 8017 8.2), the signature JWT calls RS256."""
    n, d = rsa_key(pem)
    k = (n.bit_length() + 7) // 8
    t = SHA256_INFO + hashlib.sha256(data).digest()
    em = b'\x00\x01' + b'\xff' * (k - len(t) - 3) + b'\x00' + t
    return pow(int.from_bytes(em, 'big'), d, n).to_bytes(k, 'big')


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


# ---------------------------------------------------------------- HTTP and Google

def http(method, url, headers=None, data=None):
    """(status, parsed JSON or None). Tests replace this function."""
    req = urllib.request.Request(url, data=data, method=method, headers={'User-Agent': UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            status, raw = res.status, res.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


class Google:
    """A service account of the Firebase project: an OAuth token, Firestore reads and FCM sends."""

    def __init__(self, sa):
        for k in ('project_id', 'client_email', 'private_key', 'token_uri'):
            if not sa.get(k):
                raise ValueError(f'サービスアカウントの鍵に {k} がありません')
        if not sa['token_uri'].startswith('https://oauth2.googleapis.com/'):
            raise ValueError('token_uri が Google のものではありません')
        self.sa, self.project = sa, sa['project_id']
        self._token, self._until, self._lock = None, 0, threading.Lock()

    def assertion(self, now):
        head = b64u(json.dumps({'alg': 'RS256', 'typ': 'JWT', 'kid': self.sa.get('private_key_id', '')}).encode())
        claims = b64u(json.dumps({'iss': self.sa['client_email'], 'scope': ' '.join(SCOPES),
                                  'aud': self.sa['token_uri'], 'iat': now, 'exp': now + 3600}).encode())
        return f'{head}.{claims}.{b64u(rs256(self.sa["private_key"], f"{head}.{claims}".encode()))}'

    def token(self):
        with self._lock:
            now = int(time.time())
            if self._token and now < self._until - 120:
                return self._token
            form = urllib.parse.urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                                           'assertion': self.assertion(now)}).encode()
            status, res = http('POST', self.sa['token_uri'],
                               {'Content-Type': 'application/x-www-form-urlencoded'}, form)
            if status != 200 or not (res or {}).get('access_token'):
                raise RuntimeError(f'Google の token が取れません（{status} {(res or {}).get("error", "")}）')
            self._token, self._until = res['access_token'], now + int(res.get('expires_in', 3600))
            return self._token

    def _auth(self):
        return {'Authorization': 'Bearer ' + self.token()}

    def doc_url(self, device):
        return f'{FIRESTORE}/projects/{self.project}/databases/(default)/documents/devices/{device}'

    def position(self, device):
        """The fields the phone wrote ({lat, lon, accuracy, at, token}), or None when there is no document."""
        status, res = http('GET', self.doc_url(device), self._auth())
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f'Firestore を読めません（{status}）')
        return {k: firestore_value(v) for k, v in (res.get('fields') or {}).items()}

    def forget(self, device):
        status, _ = http('DELETE', self.doc_url(device), self._auth())
        return status in (200, 404)

    def send(self, fcm_token, data):
        """Send a data-only high-priority message. 'sent', 'gone' (the phone's token is no longer valid) or an error."""
        msg = {'message': {'token': fcm_token, 'data': data,
                           'android': {'priority': 'HIGH', 'ttl': '3600s', 'collapse_key': 'notice'}}}
        status, res = http('POST', f'{FCM}/projects/{self.project}/messages:send',
                           {**self._auth(), 'Content-Type': 'application/json; charset=utf-8'},
                           json.dumps(msg, ensure_ascii=False).encode())
        if status == 200:
            return 'sent'
        err = (res or {}).get('error', {})
        if status == 404 or any(d.get('errorCode') == 'UNREGISTERED' for d in err.get('details', [])):
            return 'gone'
        return f'error {status} {err.get("status", "")}'


def firestore_value(v):
    if 'doubleValue' in v:
        return float(v['doubleValue'])
    if 'integerValue' in v:
        return int(v['integerValue'])
    if 'stringValue' in v:
        return v['stringValue']
    return None


# ---------------------------------------------------------------- 知らせ → push

def push_key(out):
    """What the phone has been told: area, the triggering warnings, the zones the spot is in and the newest bear record.
    A new report of the same warnings is not news; a higher level, another area, another zone or a newer record is."""
    warn, n = out.get('warning') or {}, out['notice']
    codes = sorted({w['code'] for w in warn.get('warnings', []) + warn.get('flood', []) if w['group1'] and w['level'] >= 3})
    parts = [out['area']['class20']['code'], ','.join(codes), ','.join(sorted(n.get('places', []))),
             'demo' if out.get('demo') else '']
    if n.get('wildlife'):  # 記録そのもの。地点との距離は携帯が少し動くだけで変わるので入れない
        parts.append(f"bear:{n['wildlife']['time']}@{n['wildlife']['link']}")
    return '|'.join(parts)


def links_for(out):
    """Official pages for the notice: the warning pages, the キキクル maps of the hazards, the hazard map,
    and for a bear record the prefecture's map and the spot of the record."""
    warn, n = out.get('warning') or {}, out['notice']
    trig = [w for w in warn.get('warnings', []) + warn.get('flood', []) if w['group1'] and w['level'] >= 3]
    hazards = {w['hazard'] for w in trig}
    links = [['気象庁の警報・注意報', warn['link']]] if trig else []
    if 'flood' in hazards:
        links.append(['指定河川洪水予報', warn['flood_link']])
    kiki_keys = {k for h in hazards for k in app.LINKS[h]['kiki']}
    for k in out.get('kikikuru') or []:
        if k['key'] in kiki_keys:
            links.append([k['label'].split('（')[0], k['link']])
    if out.get('zones') and n.get('places'):
        links.append(['重ねるハザードマップ', out['zones']['link']])
    b = n.get('wildlife')
    if b:
        for src in (out.get('wildlife') or {}).get('sources', []):
            if src['name'] == b['source']:
                links.append([src['map_name'], src['map']])
        links.append(['記録の地点（地理院地図）', b['link']])
    return links


# 通知の下のボタン（2 つまで）。短い名前で、公式のページをそのまま開く
BUTTON_NAMES = {'気象庁の警報・注意報': '警報を開く', '指定河川洪水予報': '洪水予報', '重ねるハザードマップ': 'ハザードマップ',
                '記録の地点（地理院地図）': '記録の地点'}


def page_order(link):
    return 0 if link[0] == '気象庁の警報・注意報' else 1 if 'キキクル' in link[0] else 2


def buttons_for(links):
    """The notice's first two pages as short buttons: the warning page first, then the キキクル map."""
    return [[BUTTON_NAMES.get(name, name.split('（')[0]), url] for name, url in sorted(links, key=page_order)[:2]]


def site_line(name, area):
    """A page's name for the notice's body, with the place it is about (the links themselves are the buttons)."""
    if name == '気象庁の警報・注意報':
        return f'{area}の警報・注意報（気象庁）'
    if 'キキクル' in name or name == '指定河川洪水予報':
        return f'この場所の{name}（気象庁）'
    if name == '重ねるハザードマップ':
        return 'この場所の重ねるハザードマップ'
    return name


FACT_ORDER = ('zone', 'kiki', 'wildlife', 'road', 'rain', 'lore')  # what is at this spot, nearest first


def fact_lines(out):
    """The facts for the phone, most important first: the warnings by their official names, so the level is
    said in words and does not rest on the color (TalkBack does not read colors), then what is at this spot.
    Official facts only, the same ones the PC shows."""
    n, warn = out['notice'], out.get('warning') or {}
    trig = sorted((w for w in warn.get('warnings', []) + warn.get('flood', []) if w['group1'] and w['level'] >= 3),
                  key=lambda w: -w['level'])
    names = dict.fromkeys(app.warning_name(w['hazard'], w['level']) + ('（試し）' if w.get('demo') else '')
                          for w in trig)
    lines = [f"{n.get('area', '')}に{'、'.join(names)}"] if names else []
    facts = [f for f in n.get('facts', []) if f.get('line') and f['id'] != 'warning']
    facts.sort(key=lambda f: next((i for i, k in enumerate(FACT_ORDER) if f['id'].startswith(k)), len(FACT_ORDER)))
    return lines + [f['line'] for f in facts]


def message_for(out, age=None):
    """The FCM data (strings only) for a notice, cut to fit the FCM size limit.

    The body says the facts first (the warning by its official name, then what is at this spot), then names
    the official pages for this place in the buttons' order. The folded line is the first fact. The app's
    screen shows the facts and the tail as `notes`, above its list of pages. The tail (the 試し note, the age
    of the position) always stays; when it does not fit, the last pages go first, then the last facts."""
    n = out['notice']
    tail = list(n.get('notes', []))
    links = links_for(out)
    # the app lists the pages under the same names as the notice's body
    pages = [[site_line(name, n.get('area', '')), url] for name, url in sorted(links, key=page_order)]
    facts = fact_lines(out)
    lines = [name for name, _ in pages][:PHONE_LINES]
    if age is not None and age > OLD_POSITION:
        tail.append(f'位置は {age // 3600} 時間 {age % 3600 // 60} 分前のもの')
    data = {'title': n['title'], 'area': n.get('area', ''), 'links': json.dumps(pages, ensure_ascii=False),
            'buttons': json.dumps(buttons_for(links), ensure_ascii=False),
            'level': str(n.get('level', 0)), 'checked': out['checked_at'], 'demo': '1' if out.get('demo') else '0',
            'summary': (facts or lines or tail)[0]}
    while True:
        data['body'] = '\n'.join(facts + lines + tail)
        data['notes'] = '\n'.join(facts + tail)
        if len(json.dumps(data, ensure_ascii=False).encode()) <= DATA_LIMIT:
            return data
        if len(lines) > 1:
            lines.pop(-1)
        elif len(facts) > 1:
            facts.pop(-1)
        else:
            return data


# ---------------------------------------------------------------- 状態（.phone/phone.json、このユーザーだけが読める）

def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {'devices': {}, 'last': {}}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), mode=0o700, exist_ok=True)
    tmp = STATE + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def read_google_services(path):
    """project, sender, app id and API key of the Android app PACKAGE in google-services.json."""
    with open(path) as f:
        gs = json.load(f)
    info = gs['project_info']
    for c in gs.get('client', []):
        if c['client_info']['android_client_info']['package_name'] == PACKAGE:
            return {'projectId': info['project_id'], 'senderId': info['project_number'],
                    'appId': c['client_info']['mobilesdk_app_id'], 'apiKey': c['api_key'][0]['current_key']}
    raise ValueError(f'google-services.json に Android アプリ {PACKAGE} がありません')


def pair(google_services, name):
    """Issue a device id for one phone and return the setup text to paste into the app."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', name):
        raise ValueError('名前は英数字と - _ で 32 文字まで')
    cfg = read_google_services(google_services)
    state = load_state()
    if state.get('project') not in (None, cfg['projectId']):
        raise ValueError(f"いまは別の Firebase プロジェクト {state['project']} とつないでいます")
    device = secrets.token_urlsafe(32)
    state['project'] = cfg['projectId']
    state['devices'][name] = {'id': device, 'added': datetime.now(JST).isoformat(timespec='seconds')}
    state['last'].pop(name, None)
    save_state(state)
    return json.dumps({'v': 1, **cfg, 'device': device}, separators=(',', ':'))


def log(text):
    print(f"{datetime.now(JST).strftime('%m/%d %H:%M:%S')} {text}", flush=True)


def check_device(g, state, name, demo=None, force=False):
    """Read the phone's position, look it up and push when there is news. Returns what happened (no position)."""
    pos = g.position(state['devices'][name]['id'])
    if not pos or 'lat' not in pos or 'lon' not in pos:
        return 'まだ位置が届いていません'
    if not pos.get('token'):
        return '携帯の通知の宛先がまだ届いていません'
    if not in_japan(pos['lat'], pos['lon']):
        return '日本の範囲の外です'
    out = here(pos['lat'], pos['lon'], demo)
    if not out['notice'].get('notify'):
        state['last'].pop(name, None)
        return '知らせることはありません'
    key = push_key(out)
    if key == state['last'].get(name) and not force:
        return '知らせは送ったものから変わっていません'
    age = int(time.time()) - pos['at'] if isinstance(pos.get('at'), int) else None
    result = g.send(pos['token'], message_for(out, age))
    if result == 'sent':
        state['last'][name] = key
        return '送りました: ' + out['notice']['title']
    if result == 'gone':
        return '携帯の通知の宛先が使えなくなっています（アプリで「位置を送り始める」をもう一度）'
    return '送れませんでした: ' + result


def check_all(g, state, demo=None, force=False, only=None):
    for name in sorted(state['devices']):
        if only and name != only:
            continue
        try:
            log(f'{name}: ' + check_device(g, state, name, demo, force))
        except Exception as e:
            log(f'{name}: 調べられませんでした（{type(e).__name__}: {e}）')
    save_state(state)


def google_for(path, state):
    with open(path) as f:
        g = Google(json.load(f))
    if state.get('project') and state['project'] != g.project:
        raise SystemExit(f"鍵のプロジェクト {g.project} と、つないだプロジェクト {state['project']} が違います")
    return g


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('pair', help='携帯を 1 台つなぐ。アプリに貼る設定の文を出す')
    p.add_argument('--google-services', required=True, metavar='google-services.json')
    p.add_argument('--name', default='phone')
    for cmd, text in (('watch', '数分ごとに調べ、新しい知らせだけ送る'), ('test', '作りものの警報で、いま 1 回送る')):
        p = sub.add_parser(cmd, help=text)
        p.add_argument('--key', required=True, metavar='service-account.json')
        p.add_argument('--demo', type=parse_demo, default=('landslide', 4) if cmd == 'test' else None,
                       metavar='HAZARD[:LEVEL]', help='作りものの警報を足す（test は landslide:4 が既定）')
        p.add_argument('--name', help='この携帯だけ')
        if cmd == 'watch':
            p.add_argument('--every', type=int, default=180, help='調べる間隔（秒、60 以上）')
    sub.add_parser('list', help='つないだ携帯の名前')
    p = sub.add_parser('forget', help='携帯を外す（Firestore の位置も消す）')
    p.add_argument('name')
    p.add_argument('--key', required=True, metavar='service-account.json')
    a = ap.parse_args()
    state = load_state()
    if a.cmd == 'pair':
        text = pair(a.google_services, a.name)
        print(f'携帯「{a.name}」の設定の文です。アプリの「設定」の欄に貼り、「設定を保存」を押します（ほかの人に見せない）:\n')
        print(text)
        return
    if a.cmd == 'list':
        for name, d in sorted(state['devices'].items()):
            print(name, d['added'])
        return
    g = google_for(a.key, state)
    if a.cmd == 'forget':
        d = state['devices'].pop(a.name, None)
        if not d:
            raise SystemExit(f'{a.name} はつないでいません')
        state['last'].pop(a.name, None)
        print('Firestore の位置を消しました' if g.forget(d['id']) else 'Firestore の位置を消せませんでした')
        save_state(state)
        return
    if not state['devices']:
        raise SystemExit('つないだ携帯がありません（先に pair）')
    if a.cmd == 'test':
        check_all(g, state, a.demo, force=True, only=a.name)
        return
    every = max(60, a.every)
    log(f'{every} 秒ごとに調べます（Ctrl+C で止まります）')
    try:
        while True:
            check_all(g, state, a.demo, only=a.name)
            time.sleep(every)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
