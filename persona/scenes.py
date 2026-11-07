"""The scenes the personas go through: what サークル shows at five places, as plain text.

python3 scenes.py            -> scenes/<id>.json (the app's own output) and scenes/<id>.txt (what a person sees)
python3 scenes.py --reuse    -> only re-render the text from the saved JSON (no network, no Claude)
python3 scenes.py --renotice -> rebuild the notice from the saved facts with today's code (the same facts as the
                                last round, so two rounds compare the app, not the weather), then compose it again

With ANTHROPIC_API_KEY set, the notice's body is written by Claude as in the real app (one call per scene).
Scene 0 is the first start of the phone app, read from its strings.xml.
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'app'))
import app      # noqa: E402
import compose  # noqa: E402
import phone    # noqa: E402

OUT = os.path.join(HERE, 'scenes')
STRINGS = os.path.join(HERE, '..', 'phone', 'app', 'src', 'main', 'res', 'values', 'strings.xml')

# 公開の場所だけ。demo は作りものの警報（本番で出たときの見え方を再現する）。秋田は本物のクマの記録
SCENES = [
    {'id': 's1', 'where': '東京都新宿区、新宿駅の近く', 'point': (35.6895, 139.6917), 'demo': 'flood:3'},
    {'id': 's2', 'where': '東京都墨田区、押上駅の近く', 'point': (35.7101, 139.8132), 'demo': 'flood:4'},
    {'id': 's3', 'where': '広島市の山ぞいの住宅地', 'point': (34.47, 132.47), 'demo': 'landslide:4'},
    {'id': 's4', 'where': '名古屋市港区、海に近い所', 'point': (35.0897, 136.8836), 'demo': 'tide:4'},
    {'id': 's5', 'where': '秋田市の住宅地', 'point': (39.7375, 140.132), 'demo': None},
]

COLORS = {'5': '黒（題の文字は色なし）', '4': '紫', '3': '赤', '2': '黄（題の文字は色なし）'}
DEMO_NOTE = '警報は試しの作りもの。ほかは本物'


def strings():
    return {e.get('name'): (e.text or '') for e in ET.parse(STRINGS).getroot().iter('string')}


def production(text):
    """The demo's own marks taken out, so the persona reads it as a real warning would look."""
    text = re.sub(r'（これは試し[^）]*）', '', text).replace('（試しの作りもの）', '').replace('（試し）', '')
    text = text.replace('（ためし）', '').replace(' (TEST)', '')
    return '\n'.join(line for line in text.split('\n') if line != DEMO_NOTE)


def scene0():
    s = strings()
    return '\n'.join([
        '## 場面 0: はじめて使う',
        f'携帯にアプリ「{s["app_name"]}」を入れて開いたところです。家族や支援の人が手伝ってもかまいません。',
        '',
        '### アプリの画面（上から順に）',
        f'- 見出し「{s["section_notice"]}」: {s["no_notice"]}',
        f'  {s["no_notice_about"]}',
        f'- 見出し「{s["section_watch"]}」: 状態の札「設定がまだです」',
        f'- 見出し「{s["section_setup"]}」: {s["setup_intro"]}',
        f'  入力欄（うすい字で「{s["setup_hint"]}」）、ボタン「{s["save_setup"]}」',
        f'- ボタン「{s["start"]}」「{s["stop"]}」',
        f'- 小さい字: {s["privacy"]}',
        '',
        f'### 「{s["start"]}」を押したあとに出る確かめ',
        f'題「{s["background_title"]}」: {s["background_text"]}',
        f'ボタン「{s["later"]}」「{s["next"]}」。次の画面は Android の設定画面で、位置の許可を選びます。',
        '',
        '### 携帯の設定に出る、通知の種類の名前',
        f'「{s["channel"]}」: {s["channel_about"]}',
        '',
        '### PC の側',
        '家の PC で「python3 app.py」を動かし、ブラウザで http://127.0.0.1:8765/ を開くと、いまの場所の情報が出ます。'
        '携帯とつなぐ設定の文は、PC で「python3 phone.py pair」を動かすと出ます。',
    ])


def render(sc, out, msg):
    n = out['notice']
    title = production(n['title'])
    body = production(msg['body'])
    pages = json.loads(msg['links'])
    buttons = json.loads(msg['buttons'])
    color = COLORS.get(msg['level'], 'なし')
    pc = production(n['body'])
    comp = n.get('composer') or {}
    by = (f'この文は Claude（{comp["by"]}）が公式の事実だけから組み立て、アプリが確かめたものです。' if comp.get('ok')
          else '公式の事実を並べた文です。')
    L = [f'## 場面 {sc["id"][1:]}: {sc["where"]}',
         f'あなたはいま {sc["where"]} にいます（出かけた先かもしれません）。携帯に知らせが届きました。',
         '',
         '### 届き方',
         '- 通知の種類は「公式情報」（重要度: 高）。音・振動・画面の上に出るかは、その携帯の設定のとおり。',
         '- 画面が消えていればロック画面に出ます。',
         '',
         '### 通知（たたんだまま）',
         f'アプリ名「サークル」 / 題「{title}」（題の文字の色: {color}） / 1 行目「{production(msg["summary"])}」',
         '',
         '### 通知（ひろげたとき）',
         f'題「{title}」',
         '本文:',
         *[f'  {line}' for line in body.split('\n') if line],
         f'ボタン: {" / ".join("「" + b[0] + "」" for b in buttons)}（押すとその公式のページがブラウザで開きます）',
         '',
         '### 読み上げ（TalkBack などを使う人）',
         '- 題と本文の文字がそのまま読まれます。色は読まれません。ボタンは名前が読まれます。',
         '',
         '### 通知を押して開いた、アプリの画面',
         f'- 見出し「最後に届いた知らせ」、色の帯（{color}）',
         f'- 題「{title}」',
         f'- 小さい字「{n.get("area", "")}・{out["checked_at"][11:]} に届きました」',
         *[f'- {line}' for line in production(msg['notes']).split('\n') if line],
         '- 見出し「公式のページ」（押すと開く）: ' + ' / '.join(p[0] for p in pages),
         f'- 小さい字「PC が調べた時刻 {out["checked_at"][5:].replace("-", "/")}」',
         '',
         '### 家の PC の画面（この人か家族が見る。いちばん上の枠）',
         f'題「{title}」',
         *[f'  {line}' for line in pc.split('\n') if line],
         f'  （小さい字）{by}',
         '  その下に、警報・注意報、ハザードマップ、キキクル、降水ナウキャスト、伝承碑、道路冠水想定箇所、クマ等の出没の欄が続きます。']
    return '\n'.join(L)


def build(sc, client):
    lat, lon = sc['point']
    out = app.here(lat, lon, app.parse_demo(sc['demo']) if sc['demo'] else None)
    if not out['notice'].get('notify'):
        raise SystemExit(f'{sc["id"]}: 知らせが出ませんでした: {out["errors"]}')
    out['notice'] = compose.compose(out['notice'], client)
    return out


def renotice(out, client):
    """The saved scene's notice built again by today's app.build_notice, from the facts saved with it."""
    area = out['area']
    table = app.fetch_json(app.JMA + '/bosai/common/const/area.json', app.DAY)
    c20 = area.get('class20') or {}
    if c20.get('code') in table['class20s']:
        t = table['class20s'][c20['code']]
        c20.update({'kana': t.get('kana', ''), 'enName': t.get('enName', '')})
    office = area.get('office') or {}
    if office.get('code') in table['offices']:
        office['enName'] = table['offices'][office['code']].get('enName', '')
    n = app.build_notice(area, out['warning'], (out['zones'] or {}).get('items'), out['kikikuru'], out['nowcast'],
                         (out['lore'] or {}).get('items'), out['road_flood'], out['wildlife'])
    out['notice'] = compose.compose(n, client)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--reuse', action='store_true', help='保存した JSON から文だけ作り直す')
    ap.add_argument('--renotice', action='store_true', help='保存した事実から、いまのコードで知らせを作り直す')
    ap.add_argument('--only', help='この場面だけ（例 s3）')
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    client = None if a.reuse else compose.client_from_env()
    if not a.reuse and client is None:
        print('ANTHROPIC_API_KEY が無いので、本文は公式の事実を並べた文です', file=sys.stderr)
    texts = [scene0()]
    for sc in SCENES:
        path = os.path.join(OUT, sc['id'] + '.json')
        if a.reuse or (a.only and a.only != sc['id']):
            with open(path, encoding='utf-8') as f:
                out = json.load(f)
        else:
            if a.renotice:
                with open(path, encoding='utf-8') as f:
                    out = renotice(json.load(f), client)
            else:
                out = build(sc, client)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
            c = out['notice'].get('composer') or {}
            print(f'{sc["id"]}: {out["notice"]["title"]} / composer ok={c.get("ok")} {c.get("reason", "")}',
                  file=sys.stderr)
        t = render(sc, out, phone.message_for(out))
        with open(os.path.join(OUT, sc['id'] + '.txt'), 'w', encoding='utf-8') as f:
            f.write(t + '\n')
        texts.append(t)
    with open(os.path.join(OUT, 'all.txt'), 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(texts) + '\n')


if __name__ == '__main__':
    main()
