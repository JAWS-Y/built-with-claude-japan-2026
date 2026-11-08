"""Look rounds: simulated people see the real phone screens of サークル.

The away test (the default, 2026-11-07) measures only the app's purpose: does the person get away from the danger,
or keep away from it, and where do they stop. Each simulated person gets a place by lot (in danger now / about to
pass a danger / safe now but may go out). Two calls per person:
  1. notice: screen 1 only. The person sets the scene, what the official advice means there (safe_move) and the
     phone settings, then says whether they notice the notice within the hour and how far they look.
  2. away: only the screens that person reached, with answer 1 passed on unchanged. The person lives the first hour
     through 6 stages (notice, understand, believe, about me, decide, act): where they stop, what they do and when,
     and whether they get away. Then, as an advisor in that person's place with all the model knows, it says how
     the notice would reach this person (fit) and gives 1-5 ideas for サークル, each with every app rule it clashes
     with. All 4 screens may be used for that part.
People choose the ideas; the AI drops none. If call 2 fails, its .err.json keeps answer 1 and the next run reuses it
(only when the files, set, shots, model and place are the same).

python3 look.py run --set after3 --round away-after3 [--only P001,P002] [--first 5] [-j 4] [--model opus]
python3 look.py tally away-after3                 -> who got away, where people stopped; writes runs/<last>/tally.md
python3 look.py ideas --round away-after3         -> one text call groups the ideas; writes ideas.json, ideas.md, fit.md

The corners test (kept to re-read the old look-* rounds): each person sees all 4 screens and lists the 角 they hit.

python3 look.py run --test corners --set before --round look-before
python3 look.py tally look-before look-after

Images: look/shots/<set>-1-collapsed.png, -2-expanded.png, -3-app.png, -4-app-down.png (emulator, 1080x2400).
One `claude -p` call per question with the images inline (stream-json input), no tools, no MCP, no settings and no
saved session. ANTHROPIC_API_KEY is dropped, so it never falls back to API credit. People already done are
skipped. These are simulated people, not real users.
"""
import argparse
import base64
import collections
import concurrent.futures as cf
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOOK = os.path.join(HERE, 'look')
sys.path.insert(0, HERE)
import personas  # noqa: E402

SHOTS = [('1-collapsed', '画面 1: 知らせが届き、上から引き下ろしたところ（たたんだ形）'),
         ('2-expanded', '画面 2: 同じ知らせを、ひらいた形'),
         ('3-app', '画面 3: 知らせを押して、サークルの画面を開いたところ'),
         ('4-app-down', '画面 4: サークルの画面を、下へ送ったところ')]


def images(which):
    out = []
    for name, label in SHOTS:
        with open(os.path.join(LOOK, 'shots', f'{which}-{name}.png'), 'rb') as f:
            data = f.read()
        out.append((label, data))
    return out


# The away test: where the person is when the notice comes, dealt by lot so the three kinds are balanced (34/33/33
# for 100 people) and do not follow the order of personas.json. The model writes the concrete scene.
PLACES = [('いま危ない所', 'いま居る所が危ない所（川の近く・低い土地・地下・アンダーの近くなど）'),
          ('これから通る', 'これから危ない所（低い道・アンダー・川べりなど）を通る・行く'),
          ('このあと出かける', 'いまは危なくない所にいるが、このあと低い道・アンダー・川べりなどを通って出かけるかもしれない'
                           '（買い物・迎え・仕事など）')]


def places(people):
    """Deal the place types in turn inside each group of mother tongue (Japanese or not) x body (none or some), so a
    place type does not lean to one kind of person. The order inside a group is a fixed hash, not personas.json."""
    def key(p):
        return hashlib.sha256((p['id'] + 'place').encode()).hexdigest()

    groups = collections.defaultdict(list)
    for p in people:
        groups[(p['lang'].startswith('日本語'), p['body'] == 'なし')].append(p)
    out, i = {}, 0
    for g in sorted(groups):
        for p in sorted(groups[g], key=key):
            out[p['id']] = PLACES[i % len(PLACES)][0]
            i += 1
    return out


def image_part(data):
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                        'data': base64.b64encode(data).decode()}}


def shot_parts(shots, idx):
    out = []
    for i in idx:
        label, data = shots[i]
        out += [{'type': 'text', 'text': label}, image_part(data)]
    return out


def person_head(person, place):
    return ['# あなたが演じる人', personas.describe(person), '', '# この人がいる所（くじ）', dict(PLACES)[place], '']


def fmt_min(m):
    return int(m) if isinstance(m, float) and m.is_integer() else m


def message(person, shots):
    """The corners test: all 4 screens (the same text as the look-* rounds were asked with)."""
    head = ['# あなたが演じる人', personas.describe(person), '', '# 画面の写し（4 枚）']
    content = [{'type': 'text', 'text': '\n'.join(head)}] + shot_parts(shots, range(len(shots)))
    content.append({'type': 'text', 'text': 'この人として、画面 1 から 4 までを見て答えてください。'})
    return user_line(content)


def message_notice(person, shots, place):
    """The away test, call 1: screen 1 only."""
    content = [{'type': 'text', 'text': '\n'.join(person_head(person, place) + ['# 画面の写し（画面 1）'])}]
    content += shot_parts(shots, [0])
    content.append({'type': 'text', 'text': 'この人として、知らせが届いたところから答えてください。'})
    return user_line(content)


# What a person saw, by how far they looked in call 1 (indexes into SHOTS). Opening the app goes from screen 1 to 3,
# so screen 2 (the expanded notice) is not in that path.
LOOK_SHOTS = {'気づかなかった': [], '見たが、そのまま': [0], 'ひらいた（画面 2）': [0, 1],
              '押してアプリを開いた（画面 3・4）': [0, 2, 3]}


# The real notice text (no test marks), from the app's own code for this scene (app.py first_fit / KANA_FOLD,
# phone.py kana and English lines). The shots carry the test marks; the person reads only what they saw.
REAL_FOLDED = ['題: 新宿区 レベル3 氾濫警報', '題の下の行: かわの はんらん けいほう / Flood']
REAL_BODY = ['新宿区　気象庁 11/7 午後8:45 発表', '初台アンダーの冠水に警戒してください',
             'しんじゅくく に れべる3 はんらん（かわの みずが あふれる） けいほう。'
             'ちかくの みちが みずに つかる ことに けいかい して ください',
             'Shinjuku City: Level 3 Warning (Flood). Be on alert for road flooding nearby.']
REAL_ROAD = ['道路　冠水想定 4 か所（3km 以内）、近いのは初台アンダー 0.84km']


def real_lines(look):
    """The real text of what this person saw, in screen order. Nothing when they saw nothing."""
    seen = LOOK_SHOTS[look]
    if not seen:
        return []
    lines = ['画面 1（通知）'] + [f'- {t}' for t in REAL_FOLDED]
    if 1 in seen:
        lines += ['画面 2（ひらいた通知）: 題は同じ。本文は上から'] + [f'- {t}' for t in REAL_BODY]
    if 2 in seen:
        lines += ['画面 3（アプリ）: 題は同じ。枠の中は上から'] + [f'- {t}' for t in REAL_BODY + REAL_ROAD]
    return (['# この人が見た知らせの本物の文（写しの「試し」「TEST」「作りもの」の印は、この試しのためだけのもの）']
            + lines + [''])


def notice_lines(n):
    if n['noticed']:
        got = f'{fmt_min(n["minutes"])} 分後に気づいた' if n['minutes'] is not None else '気づいた（分は分からない）'
    else:
        got = '気づかなかった'
    return ['# 知らせを見たところまで（一回目の答え）', f'- 場面: {n["scene"]}', f'- くじの型に: {n["place_fit"]}',
            f'- safe_move: {n["safe_move"]}', f'- 携帯: {n["phone"]}', f'- 気づいたか: {got}',
            f'- どうやって: {n["how"]}', f'- どこまで見たか: {n["look"]}', f'- なぜ: {n["why"]}', '']


def message_away(person, shots, place, n):
    """The away test, call 2: answer 1 unchanged, the screens this person saw, then the rest for the advisor part."""
    seen = LOOK_SHOTS[n['look']]
    head = person_head(person, place) + notice_lines(n) + real_lines(n['look']) + ['# この人が見た画面']
    content = [{'type': 'text', 'text': '\n'.join(head)}]
    if seen:
        content += shot_parts(shots, seen)
    else:
        content.append({'type': 'text', 'text': '（この人は知らせに気づいていません。画面は見ていません）'})
    rest = [i for i in range(len(shots)) if i not in seen]
    if rest:
        content.append({'type': 'text', 'text': '# この人が見ていない画面（2 の相談役のときだけ使う）'})
        content += shot_parts(shots, rest)
    content.append({'type': 'text', 'text': 'この人として、一回目の答えの続きを答えてください。'})
    return user_line(content)


def user_line(content):
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}}, ensure_ascii=False)


def claude(line, prompt, schema, model, timeout):
    """One `claude -p` call: one user line in, the structured answer out. No tools, MCP, settings or saved session."""
    with open(schema, encoding='utf-8') as f:
        schema_text = json.dumps(json.load(f), ensure_ascii=False)
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
    cmd = ['claude', '-p', '--model', model, '--tools', '', '--strict-mcp-config', '--setting-sources', '',
           '--no-session-persistence', '--disable-slash-commands', '--input-format', 'stream-json',
           '--output-format', 'stream-json', '--verbose', '--json-schema', schema_text,
           '--system-prompt-file', prompt]
    with tempfile.TemporaryDirectory() as cwd:
        t0 = time.time()
        p = subprocess.run(cmd, input=line + '\n', capture_output=True, text=True, cwd=cwd, env=env,
                           timeout=timeout)
        took = round(time.time() - t0, 1)
    out = None
    for ln in p.stdout.splitlines():
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        if m.get('type') == 'result':
            out = m
    if out is None:
        return None, {'rc': p.returncode, 'stdout': p.stdout[-2000:], 'stderr': p.stderr[-2000:], 'took': took}
    if out.get('is_error') or not isinstance(out.get('structured_output'), dict):
        return None, {'rc': p.returncode, 'subtype': out.get('subtype'), 'result': str(out.get('result'))[-2000:],
                      'took': took}
    return {'answer': out['structured_output'], 'cost_usd': out.get('total_cost_usd'), 'usage': out.get('usage'),
            'models': list((out.get('modelUsage') or {}).keys()), 'took': took}, None


# (prompt, schema) per call, in the order they are asked.
TESTS = {'corners': (('prompt.md', 'schema.json'),),
         'away': (('away-notice-prompt.md', 'away-notice-schema.json'), ('away-prompt.md', 'away-schema.json'))}


def paths(pair):
    return tuple(os.path.join(LOOK, n) for n in pair)


def call(line, pair, model, timeout):
    try:
        return claude(line, *paths(pair), model, timeout)
    except subprocess.TimeoutExpired:
        return None, {'timeout': timeout}


def info(res):
    return {k: res.get(k) for k in ('cost_usd', 'usage', 'models', 'took')}


def ask_corners(person, shots, model, timeout):
    return call(message(person, shots), TESTS['corners'][0], model, timeout)


def ask_away(person, shots, model, timeout, place, notice=None):
    """Two calls. notice = (answer 1, its call info) kept from an earlier run whose call 2 failed, or None."""
    if notice is None:
        res, err = call(message_notice(person, shots, place), TESTS['away'][0], model, timeout)
        if err:
            return None, dict(err, call='notice')
        notice = (res['answer'], info(res))
    else:
        notice = (notice[0], dict(notice[1], reused=True))
    res, err = call(message_away(person, shots, place, notice[0]), TESTS['away'][1], model, timeout)
    if err:
        return None, dict(err, call='away', notice=notice[0], notice_call=notice[1])
    calls = {'notice': notice[1], 'away': info(res)}
    return {'answer': res['answer'], 'notice': notice[0], 'calls': calls,
            'cost_usd': sum(c.get('cost_usd') or 0 for c in calls.values() if not c.get('reused')),
            'took': round(sum(c.get('took') or 0 for c in calls.values() if not c.get('reused')), 1),
            'models': sorted({m for c in calls.values() for m in c.get('models') or []})}, None


def sha_file(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(a):
    with open(os.path.join(HERE, 'personas.json'), encoding='utf-8') as f:
        people = json.load(f)
    shots = images(a.set)
    shots_sha = hashlib.sha256(b''.join(d for _, d in shots)).hexdigest()
    lot = places(people) if a.test == 'away' else {}  # dealt over all 100 before --only / --first
    files = {n: sha_file(os.path.join(LOOK, n)) for pair in TESTS[a.test] for n in pair}
    same = {'files_sha256': files, 'set': a.set, 'shots_sha256': shots_sha, 'model': a.model}
    if a.only:
        want = set(a.only.split(','))
        people = [p for p in people if p['id'] in want]
    if a.first:
        people = people[:a.first]
    out_dir = os.path.join(HERE, 'runs', a.round)
    os.makedirs(out_dir, exist_ok=True)
    # A round holds answers asked one way only: refuse to add answers asked with other files, shots, model or test.
    old = [r for r in load(a.round) if r.get('test') != a.test or any(r.get(k) != v for k, v in same.items())]
    if old:
        print(f'{a.round} には、ちがう問い・画面・モデルで聞いた答えが {len(old)} 人ある'
              f'（{", ".join(r["person"]["id"] for r in old[:5])}…）。新しい --round を使ってください。',
              file=sys.stderr)
        return 2
    todo = [p for p in people if not os.path.exists(os.path.join(out_dir, p['id'] + '.json'))]
    print(f'{a.round} ({a.test}, {a.set}): {len(todo)} 人（済み {len(people) - len(todo)}）, model {a.model}, '
          f'-j {a.j}', file=sys.stderr)

    def reuse_notice(err_path, place):
        """Answer 1 from an earlier run whose call 2 failed, only when everything it was asked with is the same."""
        try:
            with open(err_path, encoding='utf-8') as f:
                e = json.load(f)
        except (OSError, ValueError):
            return None
        if e.get('call') != 'away' or not isinstance(e.get('notice'), dict) or e.get('place') != place:
            return None
        if any(e.get(k) != v for k, v in same.items()):
            return None
        return e['notice'], e.get('notice_call') or {}

    def one(p):
        err_path = os.path.join(out_dir, p['id'] + '.err.json')
        place = lot.get(p['id'])
        if a.test == 'away':
            res, err = ask_away(p, shots, a.model, a.timeout, place, reuse_notice(err_path, place))
        else:
            res, err = ask_corners(p, shots, a.model, a.timeout)
        if err:
            err.update(same, place=place, at=time.strftime('%Y-%m-%d %H:%M:%S'))
            with open(err_path, 'w', encoding='utf-8') as f:
                json.dump(err, f, ensure_ascii=False, indent=1)
            return p['id'], None, err
        res.update({'person': p, 'test': a.test, 'at': time.strftime('%Y-%m-%d %H:%M:%S'), **same})
        if a.test == 'away':
            res['place'] = place
            res['check'] = check_away(res['answer'], place, res['notice'])
        with open(os.path.join(out_dir, p['id'] + '.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        if os.path.exists(err_path):
            os.remove(err_path)
        return p['id'], res, None

    def short(err):
        keep = {k: err[k] for k in ('call', 'rc', 'subtype', 'timeout') if k in err}
        tail = (err.get('result') or err.get('stderr') or '')[-200:]
        return json.dumps(keep, ensure_ascii=False) + (f' {tail}' if tail else '')

    total, failed, ng_total = 0.0, 0, 0.0
    with cf.ThreadPoolExecutor(max_workers=a.j) as ex:
        for pid, res, err in ex.map(one, todo):
            if err:
                failed += 1
                # Call 1 ran and was paid for even when call 2 failed (unless it was reused from an earlier run).
                nc = err.get('notice_call') or {}
                if err.get('call') == 'away' and not nc.get('reused'):
                    ng_total += nc.get('cost_usd') or 0
                ng_total += err.get('cost_usd') or 0
                print(f'{pid}: NG {short(err)}', file=sys.stderr)
                continue
            total += res.get('cost_usd') or 0
            ans = res['answer']
            if a.test == 'away':
                what = (f'{res["place"]} → 見た {res["notice"]["look"]} / {ans["outcome"]}（{ans["because"]}）/ '
                        f'止まった {ans["stop"]} / 案 {len(ans["ideas"])}'
                        + (f' / 食いちがい {len(res["check"])}' if res['check'] else ''))
            else:
                what = f'角 {len(ans["corners"])}'
            print(f'{pid}: {what}, {res["took"]}s, ${res.get("cost_usd") or 0:.3f}', file=sys.stderr)
    print(f'done: {len(todo) - failed} ok, {failed} NG, 計 ${total:.2f}'
          + (f'、NG で使った分 ${ng_total:.2f}' if failed else '') + '（Max では使った量の目安）', file=sys.stderr)
    return 1 if failed else 0


# The four look corners found on the emulator (11-07) and fixed in e251a67. The kind alone is not enough: after the
# fix the cut and the line break moved to another place, so each row matches what is cut or split (quote and why).
LEVEL_CUT = re.compile(r'レベ…|レベルの数字|レベルいくつ|数字が見えない|「3」が切れ|3」が切れ|数字の3が')
MID_WORD = re.compile(r'初\s*[\n／/ ]\s*台|「初」と「台」|みず\s*[\n／/]\s*が|「みず」と「が」')
PAGE_NAME = re.compile(r'ボタン|ページの名|リンク|（気象庁）|洪水予報')
# The stop button: "止める" alone was read as "stop the sound" and it ends the notices too. Now "見守りを止める" with
# a confirm; any corner that quotes the button (old or new label, or the privacy line naming it) counts here.
STOP = re.compile(r'止める')


def _text(c):
    return c['quote'] + ' ' + c['why']


FIXED = [('題が切れて「気をつけて」が見えない',
          lambda c: c['screen'] == 1 and c['kind'] == '文字が切れる' and not LEVEL_CUT.search(_text(c))),
         ('（残り）題が切れてレベルの数字が見えない',
          lambda c: c['screen'] == 1 and c['kind'] == '文字が切れる' and bool(LEVEL_CUT.search(_text(c)))),
         ('語が行の途中で割れる（みず／が、初／台）',
          lambda c: c['kind'] == '行の変わり目' and bool(MID_WORD.search(_text(c)))),
         ('（残り）語と語の間で行が変わる',
          lambda c: c['kind'] == '行の変わり目' and not MID_WORD.search(_text(c))),
         ('位置が古い', lambda c: c['kind'] == '古い情報' or '前のもの' in c['quote']),
         ('ページの名前が本文とボタンで二度出る',
          lambda c: c['kind'] == '同じものが何度も' and bool(PAGE_NAME.search(_text(c)))),
         ('（別のもの）同じ文が漢字・ひらがな・英語で並ぶ',
          lambda c: c['kind'] == '同じものが何度も' and not PAGE_NAME.search(_text(c))),
         ('「止める」ボタン（何を止めるか分からない、音を止めると取りちがえる）',
          lambda c: bool(STOP.search(c['quote'])))]


# The simulated people try to take away every complaint: each lists about 12 corners whatever the screens show. A
# person then reads the corners and chooses what to fix, what to keep on purpose and what not to do, by the app's
# rules. CHOICES is that review: (decision, what the corners ask, why, who decided, matcher). A corner is counted in
# the first row that matches, so the rows add up to the corner count; the matchers are keyword rules (a rough guide).
ACT = re.compile(r'逃げ|避難|すぐ.{0,6}(動|離れ|行)|何をすれば|どうすれば|どう動|行動|高い所|上の階')
ROAD = re.compile(r'冠水想定|アンダー')

# The corners the 11-07 rebuild aimed at (research/ui-ux-20261107.md): what each one names, matched in the quote, why
# and fix hint. Keyword rules, a rough guide like the rows above.
WHEN = re.compile(r'いつ(?!も)|発表(時刻|の時刻|時間|された時)')
REBUILT = [('🌊 を津波と読む', lambda c: '津波' in _text(c)),
           ('試しの行「ほかは本物」が分からない', lambda c: 'ほかは本物' in _text(c)),
           ('試し／TEST を練習・訓練と読む', lambda c: bool(re.search(r'練習|訓練', _text(c)))),
           ('何をすればよいか書いてない', lambda c: bool(ACT.search(c['fix_hint'] + ' ' + c['why']))),
           ('いつの知らせか分からない', lambda c: bool(WHEN.search(_text(c) + ' ' + c['fix_hint']))),
           ('たたんだ形（題と 1 行）が読めない（漢字・読み、言語）',
            lambda c: c['where'] in ('通知の題', 'たたんだ1行') and c['kind'] in ('漢字・読み', '言語'))]

CHOICES = [
    ('直した', '見た目の 4 つ（題が切れる、語が行の途中で割れる、位置が古い、ページの名前が二度出る）',
     '写しを見て Joe が「見た目も直して」', 'Joe', lambda c: any(FIXED[i][1](c) for i in (0, 2, 4, 5))),
    ('直した', 'レベルの数字が切れる、「止める」ボタン（残りは新しい「見守りを止める」への角）',
     'Joe が「2 つを直して」', 'Joe', lambda c: any(FIXED[i][1](c) for i in (1, 7))),
    ('直した', '試しの行（作りもの・本物）がまぎらわしい',
     '作りものの警報を本物と取りちがえないための行なので消さない。11-07 に、試しの印を 1 か所にし、「ほかは本物」をやめて'
     '本物の行を名指しした（調べもの 5）', 'Joe（「作り直して」）', lambda c: '作りもの' in c['quote'] or '本物' in c['quote']),
    ('直した', '同じ文が漢字・ひらがな・英語で並ぶ',
     'ひらがなだけ、英語だけ読む人に届くための行なので消さない。11-07 に、同じ警報を 1 回ずつにし、ひらがな・英語の行は'
     'その言葉の人だけが読む短い行にした（調べもの 4）', 'Joe（「作り直して」）', lambda c: FIXED[6][1](c)),
    ('まだ', 'ほかの言葉（中国語・ベトナム語・ミャンマー語など）で読みたい',
     '画面には公式の事実だけを出すので、英語も気象庁の公式の英語だけ。公式の多言語辞書を入れるかは Joe が決める', 'Joe（まだ）',
     lambda c: c['kind'] == '言語'),
    ('一部直した', '道の行（冠水想定・アンダー）が分からない',
     '11-07 に、やさしい日本語の行で「ちかくの みちが みずに つかる」と言いかえた。「アンダー」はデータにある道の名前のまま',
     'Joe（「作り直して」）', lambda c: bool(ROAD.search(c['quote']))),
    ('一部直した', 'どう動くか（「お年寄りは逃げて」など）を書いてほしい',
     '11-07 に、気象庁の形で「〜に警戒してください」「〜に注意してください」と、何に気をつけるかを具体に書いた。'
     '逃げる・避難の指図は書かない。判断と責任は人が持つ', 'Joe（「〜に注意してください、でいいんじゃない？」）',
     lambda c: bool(ACT.search(c['fix_hint'] + ' ' + c['why']))),
]


def choose(c):
    for i, ch in enumerate(CHOICES):
        if ch[4](c):
            return i
    return len(CHOICES)


def load(rnd):
    d = os.path.join(HERE, 'runs', rnd)
    out = []
    for n in sorted(os.listdir(d)):
        if re.fullmatch(r'P\d+\.json', n):  # one file per person; not .err.json, ideas.json or ideas-raw.json
            with open(os.path.join(d, n), encoding='utf-8') as f:
                out.append(json.load(f))
    return out


def stats(rows):
    corners = [c for r in rows for c in r['answer']['corners']]
    sev = collections.Counter(c['severity'] for c in corners)
    und = collections.defaultdict(list)
    for r in rows:
        for s in r['answer']['screens']:
            und[s['screen']].append(s['understood'])
    return {
        'people': len(rows), 'corners': len(corners), 'sev': sev,
        'understood': {k: sum(v) / len(v) for k, v in sorted(und.items())},
        'kind': collections.Counter(c['kind'] for c in corners),
        'where': collections.Counter(c['where'] for c in corners),
        'fixed': [(name, sum(1 for c in corners if f(c)), len({r['person']['id'] for r in rows
                                                               for c in r['answer']['corners'] if f(c)}))
                  for name, f in FIXED],
        'rebuilt': [(name, sum(1 for c in corners if f(c)), len({r['person']['id'] for r in rows
                                                                 for c in r['answer']['corners'] if f(c)}))
                    for name, f in REBUILT],
        'quotes': collections.Counter(c['quote'].strip() for c in corners if c['quote'].strip() and c['severity'] >= 2),
        'chosen': collections.Counter(choose(c) for c in corners),
    }


def tally(a):
    st = [(r, stats(load(r))) for r in a.rounds]
    lines = ['# 見た目の回（作りものの人が画面の写しを見る）', '',
             '作りものの人（実在の人ではない）が、エミュレータで撮った画面の写し 4 枚を見て挙げた角の数。', '',
             '| | ' + ' | '.join(r for r, _ in st) + ' |', '|---|' + '---|' * len(st)]

    def row(name, f):
        lines.append(f'| {name} | ' + ' | '.join(str(f(s)) for _, s in st) + ' |')

    row('人', lambda s: s['people'])
    row('角の数', lambda s: s['corners'])
    # Each simulated person lists about the same number of corners, so the total barely moves after a fix: when one
    # corner goes, the person names another, smaller one. Read the heavy count and the fixed rows below instead.
    row('1 人あたりの角（平均）', lambda s: f"{s['corners'] / max(s['people'], 1):.1f}")
    row('重い角（3）', lambda s: s['sev'][3])
    row('角（2）', lambda s: s['sev'][2])
    row('小さい角（1）', lambda s: s['sev'][1])
    for k in range(1, 5):
        row(f'分かった度合い 画面 {k}（0–3）', lambda s, k=k: f"{s['understood'].get(k, 0):.2f}")
    lines += ['', '## 直した角と、その残り（角の数 / 挙げた人）', '', '| | ' + ' | '.join(r for r, _ in st) + ' |',
              '|---|' + '---|' * len(st)]
    for i, (name, _) in enumerate(FIXED):
        row(name, lambda s, i=i: f"{s['fixed'][i][1]} / {s['fixed'][i][2]} 人")
    lines += ['', '## 11-07 の作り直しが狙った角（角の数 / 挙げた人）', '', '| | ' + ' | '.join(r for r, _ in st) + ' |',
              '|---|' + '---|' * len(st)]
    for i, (name, _) in enumerate(REBUILT):
        row(name, lambda s, i=i: f"{s['rebuilt'][i][1]} / {s['rebuilt'][i][2]} 人")
    lines += ['', '## 人が見直して選んだこと（取捨選択）', '',
              '作りものの人は、見えた不満を全部、角として挙げる（AI はすべての角を取ろうとする）。'
              'どれを直し、どれをわざと残し、どれをしないかは、人が角を読んで決めた。どっちも要る。', '',
              '一つの角は、上の行から順に、最初に当たった一つの行にだけ数える（言葉で当てた目安）。', '',
              '| 決め | 角の中身 | ' + ' | '.join(r for r, _ in st) + ' |', '|---|---|' + '---|' * len(st)]
    for i, ch in enumerate(CHOICES + [('振り分けていない', '残り（人ごとにちがう角、小さい角）', '', '', None)]):
        lines.append(f'| {ch[0]} | {ch[1]} | ' + ' | '.join(str(s['chosen'][i]) for _, s in st) + ' |')
    lines += ['', '決めた理由と、決めた人:', '']
    lines += [f'- {ch[0]} — {ch[1]}: {ch[2]}。決めた人: {ch[3]}' for ch in CHOICES]
    kinds = sorted({k for _, s in st for k in s['kind']}, key=lambda k: -sum(s['kind'][k] for _, s in st))
    lines += ['', '## 種類', '', '| | ' + ' | '.join(r for r, _ in st) + ' |', '|---|' + '---|' * len(st)]
    for k in kinds:
        row(k, lambda s, k=k: s['kind'][k])
    wheres = sorted({k for _, s in st for k in s['where']}, key=lambda k: -sum(s['where'][k] for _, s in st))
    lines += ['', '## 場所', '', '| | ' + ' | '.join(r for r, _ in st) + ' |', '|---|' + '---|' * len(st)]
    for k in wheres:
        row(k, lambda s, k=k: s['where'][k])
    for r, s in st:
        lines += ['', f'## {r}: 多く挙がった文字（重さ 2 以上）', '']
        lines += [f'- {n} 人: 「{q[:60]}」' for q, n in s['quotes'].most_common(12)]
    text = '\n'.join(lines) + '\n'
    print(text)
    with open(os.path.join(HERE, 'runs', a.rounds[-1], 'tally.md'), 'w', encoding='utf-8') as f:
        f.write(text)

# ---- the away test: counting ----------------------------------------------------------------------------------
STAGES = ['気づく', '分かる', '信じる', '自分のことと思う', '決める', '動く']
AWAY = ('離れた', '避けた', '近づかずにいた')  # got away from the danger, or kept away from it
OUTCOMES = ('離れた', '避けた', '近づかずにいた', 'とどまった', '動けない', '近づいてしまった')
# Outcomes a place type rules out. Someone in the danger place now is in it, so cannot "keep away" (近づかずにいた);
# they may still change a plan to go somewhere worse (避けた). Someone about to pass may answer 避けた or 近づかずにいた
# (the prompt allows both when the road was not taken), so nothing is ruled out there. Someone about to go out is not
# in the danger place yet, so cannot leave it, stay in it or be stuck in it. These answers are left out of the outcome
# counts (not of the stages or the looks) and listed as mismatches. The same holds for without_notice.
NOT_FROM = {'いま危ない所': {'近づかずにいた'}, 'これから通る': set(),
            'このあと出かける': {'離れた', 'とどまった', '動けない'}}
# A rough split by the persona fields for the level-3 advice (older people, people with a body note, people with a
# small child, pregnant, carers leave first). The rest is a small group (15 of 100).
CARE = {'小さい子を連れている', '妊娠中', '親の介護をしている'}
# Going up inside the building, matched in what the people who left did (one did action at a time, a rough guide).
# Not "地下 2 階へ", and not climbing out of a basement to the street (地下・地上・外 in the same action).
UP = re.compile(r'上の階|上の部屋|屋上|階段で上|階段を上|階段をのぼ'
                r'|(?<![0-9０-９])(?<!地下)(?<!地下 )([2-9２-９二三四五六七八九]|上) ?階(の[^。、]{0,6})?[へに]')
NOT_UP = re.compile(r'地下|地上|外へ|外に')
FIT_KEYS = ['言葉', '文字', '読み上げ', '音・ふるえ', '見た目・色', '長さ', 'その他']


def first_stop(ans):
    for st in ans['steps']:
        if not st['passed']:
            return st['stage']
    return '止まらなかった'


def wn_checks(ans, place=None, notice=None):
    """Mismatches in without_notice (the person's guess of what would have happened with no Circle notice). Rows with
    any of these are left out of the 見立て rows. 離れられていない (did not get away) is left free: with the notice
    the person may have done worse than without it."""
    out = []
    wn, o, because = ans['without_notice'], ans['outcome'], ans['because']
    if place and wn in NOT_FROM.get(place, ()):
        out.append(f'くじ「{place}」から without_notice「{wn}」は起きない')
    if notice is not None and notice.get('look') == '気づかなかった' and wn != o:
        out.append(f'知らせに気づかなかったのに、without_notice「{wn}」が outcome「{o}」とちがう')
    elif because in ('ほかのきっかけで', 'もともと近づかなかった') and wn != o:
        out.append(f'because「{because}」なのに、without_notice「{wn}」が outcome「{o}」とちがう')
    if because == 'サークルの知らせで' and wn in AWAY:
        out.append(f'サークルの知らせで離れられたのに、知らせが無くても「{wn}」')
    return out


def check_away(ans, place=None, notice=None):
    """Mismatches inside answer 2, and between answer 2 and answer 1 (the model's own fields that disagree). Listed,
    not fixed: the stop is always counted from the steps and the outcome as answered."""
    out = []
    steps = ans['steps']
    by = {st['stage']: st for st in steps}
    if [st['stage'] for st in steps] != STAGES:
        out.append('段階の順がちがう')
    failed = False
    for st in steps:
        if failed and st['passed']:
            out.append(f'止まったあとで「{st["stage"]}」を通っている')
            break
        failed = failed or not st['passed']
    stop = first_stop(ans)
    if ans['stop'] != stop:
        out.append(f'stop「{ans["stop"]}」と、はじめて通れなかった段階「{stop}」がちがう')
    got = ans['outcome'] in AWAY
    if got == (ans['because'] == '離れられていない'):
        out.append(f'outcome「{ans["outcome"]}」と because「{ans["because"]}」が合わない')
    if ans['because'] == 'サークルの知らせで' and stop != '止まらなかった':
        out.append(f'サークルの知らせで離れられたのに、「{stop}」で止まっている')
    # 動けない / 近づいてしまった may pass 動く when the person started and then stopped or turned back.
    if stop == '止まらなかった' and ans['outcome'] == 'とどまった':
        out.append(f'6 つの段階を通ったのに、outcome が「{ans["outcome"]}」')
    if ans['because'] == 'もともと近づかなかった' and (place == 'いま危ない所' or ans['outcome'] == '離れた'):
        out.append(f'because「もともと近づかなかった」は、くじ「{place}」・outcome「{ans["outcome"]}」には使わない')
    if ans['outcome'] in ('離れた', '避けた') and ans['minutes'] is None:
        out.append(f'{ans["outcome"]}のに、動き出した時がない')
    if ans['outcome'] in ('離れた', '避けた') and not ans['did']:
        out.append(f'{ans["outcome"]}のに、did が空')
    move = by.get('動く') or {}
    if ans['minutes'] is not None and move.get('passed') and move.get('minutes') is not None \
            and ans['minutes'] != move['minutes'] and ans['because'] != 'もともと近づかなかった':
        out.append(f'動き出した分（{fmt_min(ans["minutes"])}）と「動く」の分（{fmt_min(move["minutes"])}）がちがう')
    if place and ans['outcome'] in NOT_FROM.get(place, ()):
        out.append(f'くじ「{place}」から outcome「{ans["outcome"]}」は起きない')
    passed = [st for st in steps if st['passed']]
    if any(st['minutes'] is None for st in passed):
        out.append('通った段階に分がない')
    # Someone moved first by another trigger may pass the later stages before the notice is read (prompt 1, L32).
    other = ans['because'] == 'ほかのきっかけで'
    mins = [st['minutes'] for st in passed if st['minutes'] is not None]
    if not other and mins != sorted(mins):
        out.append('段階の分が時の順になっていない')
    dec = by.get('決める') or {}
    if not other and ans['minutes'] is not None and dec.get('passed') and dec.get('minutes') is not None \
            and ans['minutes'] < dec['minutes']:
        out.append('決めるより前に動き出している')
    did = [d['minutes'] for d in ans['did']]
    if did != sorted(did):
        out.append('did が時の順になっていない')
    if notice is not None:
        noticed = bool(notice.get('noticed'))
        first = by.get('気づく') or {}
        if bool(first.get('passed')) != noticed:
            out.append('気づく の段階が一回目と合わない')
        elif noticed and first.get('minutes') is not None and notice.get('minutes') is not None \
                and first['minutes'] != notice['minutes']:
            out.append(f'気づいた分が一回目（{fmt_min(notice["minutes"])} 分）とちがう（{fmt_min(first["minutes"])} 分）')
        if (not noticed) != (notice.get('look') == '気づかなかった'):
            out.append('一回目の noticed と look が合わない')
        if (notice.get('minutes') is None) != (not noticed):
            out.append('一回目の noticed と分が合わない')
    out.extend(wn_checks(ans, place, notice))
    return out


def lang_group(p):
    return '日本語が母語' if p['lang'].startswith('日本語') else '日本語が母語でない'


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def vulnerable(p):
    return p['age'] >= 65 or p['body'] != 'なし' or p['situation'] in CARE


def place_ok(r):
    return r['answer']['outcome'] not in NOT_FROM.get(r.get('place'), ())


def reached(ans):
    """How many stages the person passed, counted from the first stop."""
    s = first_stop(ans)
    return STAGES.index(s) if s in STAGES else len(STAGES)


def went_up(ans):
    """Did one of the person's actions go up inside the building (not out of a basement to the street)?"""
    return any(UP.search(d['action']) and not NOT_UP.search(d['action']) for d in ans['did'])


def away_stats(rows):
    ok = [r for r in rows if place_ok(r)]
    ans = [r['answer'] for r in ok]
    away = [x for x in ans if x['outcome'] in AWAY]
    left = [x for x in ans if x['outcome'] == '離れた']
    # The 見立て rows (what would have happened with no notice) count only the rows with no without_notice mismatch.
    wn = [r['answer'] for r in ok if not wn_checks(r['answer'], r.get('place'), r.get('notice'))]
    far = [reached(r['answer']) for r in rows]
    notices = [r.get('notice') or {} for r in rows]

    def moved(o):
        return median([x['minutes'] for x in ans if x['outcome'] == o and x['because'] != 'もともと近づかなかった'
                       and x['minutes'] is not None])

    def safe(group):
        return collections.Counter(r['answer']['did_safe_move'] for r in ok if vulnerable(r['person']) == group)

    def stage_min(i):
        return median([st['minutes'] for r, k in zip(rows, far) if k > i for st in r['answer']['steps']
                       if st['stage'] == STAGES[i] and st['minutes'] is not None])

    return {
        'people': len(rows), 'ok': len(ok), 'off_lot': len(rows) - len(ok), 'away': len(away),
        'because': collections.Counter(x['because'] for x in away),
        'outcome': collections.Counter(x['outcome'] for x in ans),
        'wn_n': len(wn), 'wn_away': sum(x['without_notice'] in AWAY for x in wn),
        'changed': sum(x['because'] == 'サークルの知らせで' for x in wn),
        'worse': sum(x['outcome'] not in AWAY and x['without_notice'] in AWAY for x in wn),
        'left': len(left), 'up': sum(went_up(x) for x in left),
        'min_left': moved('離れた'), 'min_avoid': moved('避けた'),
        'safe': {True: safe(True), False: safe(False)},
        'passed': [sum(k > i for k in far) for i in range(len(STAGES))],
        'stops': collections.Counter(first_stop(r['answer']) for r in rows),
        'stops_not': collections.Counter(first_stop(x) for x in ans if x['outcome'] not in AWAY),
        'stage_min': [stage_min(i) for i in range(len(STAGES))],
        'look': collections.Counter(n.get('look') for n in notices),
        'notice_min': median([n['minutes'] for n in notices if n.get('noticed') and n.get('minutes') is not None]),
        'checks': sum(bool(check_away(r['answer'], r.get('place'), r.get('notice'))) for r in rows),
    }


def pct(k, n):
    return f'{k} / {n}（{round(100 * k / n)}%）' if n else '0 / 0'


def mm(v):
    return '—' if v is None else fmt_min(v)


def width(n):
    """About how far a share of n people moves by chance alone (95%, at 50%), in points. A rough guide."""
    return f'±{round(196 * (0.25 / n) ** 0.5)} ポイント' if n else '—'


def traits(p):
    return f'{p["age"]} 歳、{p["lang"]}、{p["reading"]}、{p["body"]}'


def who(p, place):
    return f'{p["id"]}（{traits(p)}、{place}）'


def person_key(p):
    return p['body'], p['lang'], p['id']


def details(summary, body):
    return [f'<details><summary>{summary}</summary>', ''] + body + ['', '</details>', '']


def cell(t):
    return str(t).replace('|', '／').replace('\n', ' ')


def tally_away(a):
    st = [(r, rows, away_stats(rows)) for r, rows in ((r, load(r)) for r in a.rounds)]
    head = '| | ' + ' | '.join(r for r, _, _ in st) + ' |'
    sep = '|---|' + '---|' * len(st)
    lines = ['# 離れられたか、どこで止まったか、いつ何をしたか（作りものの人が、知らせを受けとってからの 1 時間）', '',
             '作りものの人（実在の人ではない。AI が演じた）が、サークルの知らせを受けとり、くじで決めたいる所から 1 時間を'
             '演じた。一人に 2 回聞いた。1 回目は、届いた知らせ（画面 1）だけを見せて、場面・携帯の設定と、気づくか・'
             'どこまで見るか。2 回目は、その人が見たところまでの画面だけを見せて、6 つの段階（気づく・分かる・信じる・'
             '自分のことと思う・決める・動く）と、いつ何をしたかと、どうなったか。', '',
             '数えたのは「危ない所から離れられたか（近づかずにいられたか）」「どこで止まったか」「いつ何をしたか」だけ。'
             'どれも演じた AI の見立てで、本当の人の動きではない。くじと合わない答え（例: いま危ない所にいるのに'
             '「近づかずにいた」）は、どうなったかの数に入れない（止まった所と、どこまで見たかには入れる）。', '']
    for r, rows, _ in st:
        for k, f in (('model', lambda x: ','.join(x.get('models') or [])),
                     ('files_sha256', lambda x: json.dumps(x.get('files_sha256'), sort_keys=True)),
                     ('set', lambda x: str(x.get('set'))), ('shots_sha256', lambda x: str(x.get('shots_sha256'))),
                     ('test', lambda x: str(x.get('test')))):
            c = collections.Counter(f(x) for x in rows)
            if len(c) > 1:
                lines += [f'注意: {r} の中で {k} のちがう答えが混ざっている（{len(c)} 種類）。', '']
    lines += [head, sep]

    def row(name, f):
        lines.append(f'| {name} | ' + ' | '.join(str(f(s)) for _, _, s in st) + ' |')

    def safe(c):
        return f'した {c["した"]}・一部 {c["一部した"]}・しなかった {c["しなかった"]}（{sum(c.values())} 人）'

    row('人', lambda s: s['people'])
    row('**離れられた人**（離れた・避けた・近づかずにいた）', lambda s: pct(s['away'], s['ok']))
    row('　うち もともと近づかなかった', lambda s: pct(s['because']['もともと近づかなかった'], s['ok']))
    row('　うち サークルの知らせで（演じた AI の見立て）', lambda s: pct(s['because']['サークルの知らせで'], s['ok']))
    row('　うち ほかのきっかけで', lambda s: pct(s['because']['ほかのきっかけで'], s['ok']))
    row('　幅の目安（人数だけから、95%）', lambda s: width(s['ok']))
    row('くじと合わない答え（上の数に入れない）', lambda s: s['off_lot'])
    row('答えに食いちがいの無い人（下の 3 つの見立ての数え先）', lambda s: s['wn_n'])
    row('　知らせが無かったら離れられた（見立て）', lambda s: pct(s['wn_away'], s['wn_n']))
    row('　知らせで、離れられた側に変わった（見立て）', lambda s: pct(s['changed'], s['wn_n']))
    row('　知らせで、離れられない側に変わった（見立て）', lambda s: pct(s['worse'], s['wn_n']))
    row('離れた人のうち、上の階へ（言葉で当てた目安）', lambda s: f"{s['up']} / {s['left']}")
    row('動き出すまで（離れた、もともとを除く、中央値・分）', lambda s: mm(s['min_left']))
    row('動き出すまで（避けた、もともとを除く、中央値・分）', lambda s: mm(s['min_avoid']))
    row('safe_move に当たることをした（65 歳以上・体のこと・子連れ・妊娠中・介護の人）', lambda s: safe(s['safe'][True]))
    row('safe_move に当たることをした（そのほかの人。人数が少ない）', lambda s: safe(s['safe'][False]))
    row('答えの中の食いちがい（人）', lambda s: s['checks'])
    lines += ['', '## どうなったか（くじと合う答え）', '', head, sep]
    for o in OUTCOMES:
        row(o, lambda s, o=o: s['outcome'][o])
    lines += ['', '## どこで止まったか（6 つの段階、全員）', '',
              '通れた人は、その段階まで止まらずに通れた人の数。止まった人は、そこで初めて通れなかった人の数。'
              '（ ）の中は、そのうち離れられなかった人（とどまった・動けない・近づいてしまった。くじと合う答えだけ）。'
              '分は、通れた人が知らせから何分でその段階を通ったか（中央値）。', '', head, sep]
    for i, stg in enumerate(STAGES):
        row(f'{stg}: 通れた', lambda s, i=i: s['passed'][i])
        row(f'{stg}: ここで止まった（うち 離れられなかった人）',
            lambda s, stg=stg: f"{s['stops'][stg]}（{s['stops_not'][stg]}）")
        row(f'{stg}: 通った分（中央値）', lambda s, i=i: mm(s['stage_min'][i]))
    row('止まらなかった（うち 離れられなかった人）',
        lambda s: f"{s['stops']['止まらなかった']}（{s['stops_not']['止まらなかった']}）")
    lines += ['', '## 一回目: 知らせをどこまで見たか（全員）', '', head, sep]
    for lk in LOOK_SHOTS:
        row(lk, lambda s, lk=lk: s['look'][lk])
    row('気づいた分（中央値）', lambda s: mm(s['notice_min']))

    for r, rows, s in st:
        def table(title, key):
            groups = collections.defaultdict(list)
            for x in rows:
                groups[key(x)].append(x)
            out = ['', f'## {r}: {title}', '',
                   '| | 人 | くじ（危・通・出） | 離れられた | うち もともと | うち サークルの知らせで | いちばん多く止まった所 '
                   '| 幅の目安 |', '|---|---|---|---|---|---|---|---|']
            for g, xs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
                ok = [x for x in xs if place_ok(x)]
                n, m = len(ok), len(xs) - len(ok)
                lot = collections.Counter(x.get('place') for x in xs)
                stops = collections.Counter(first_stop(x['answer']) for x in xs)
                stops.pop('止まらなかった', None)
                top = '、'.join(f'{k} {v}' for k, v in stops.most_common(2)) or '—'
                away = [x for x in ok if x['answer']['outcome'] in AWAY]
                orig = sum(x['answer']['because'] == 'もともと近づかなかった' for x in away)
                circ = sum(x['answer']['because'] == 'サークルの知らせで' for x in away)
                out.append(f'| {cell(g)} | {n}' + (f'（+合わない {m}）' if m else '')
                           + f' | {"・".join(str(lot[p]) for p, _ in PLACES)} | {pct(len(away), n)} | {orig} | {circ} '
                             f'| {top} | {width(n)} |')
            return out

        lines += table('いる所（くじ）ごと', lambda x: x.get('place', '?'))
        lines += table('言葉', lambda x: lang_group(x['person']))
        lines += table('母語', lambda x: x['person']['lang'])
        lines += table('日本語の読み', lambda x: x['person']['reading'])
        lines += table('体のこと', lambda x: x['person']['body'])
        lines += table('暮らし・そのとき', lambda x: x['person']['situation'])

        off = [x for x in rows if not place_ok(x)]
        if off:
            lines += ['', f'## {r}: くじと合わない答え（{len(off)} 人、どうなったかの数に入れない）', '',
                      '| くじ | ' + ' | '.join(OUTCOMES) + ' |', '|---|' + '---|' * len(OUTCOMES)]
            for pl, _ in PLACES:
                c = collections.Counter(x['answer']['outcome'] for x in off if x.get('place') == pl)
                if c:
                    lines.append(f'| {pl} | ' + ' | '.join(str(c[o]) for o in OUTCOMES) + ' |')

        lines += ['', f'## {r}: いつ何をしたか（どうなったかごと）', '']
        for o in OUTCOMES:
            xs = sorted((x for x in rows if x['answer']['outcome'] == o), key=lambda x: x['person']['id'])
            if not xs:
                continue
            n_off = sum(not place_ok(x) for x in xs)
            body = []
            for x in xs:
                did = ' → '.join(f'{fmt_min(d["minutes"])} 分 {d["action"]}' for d in x['answer']['did']) or '—'
                note = x['answer']['because'] + ('' if place_ok(x) else '、くじと合わない')
                body.append(f'- {who(x["person"], x.get("place"))}（{note}）: {did}')
            lines += details(f'{o}（{len(xs) - n_off} 人' + (f'＋くじと合わない {n_off}' if n_off else '') + '）', body)

        lines += ['', f'## {r}: 止まった所の声（この人の目から）', '']
        for stg in STAGES:
            xs = sorted((x for x in rows if first_stop(x['answer']) == stg), key=lambda x: person_key(x['person']))
            if not xs:
                continue
            body = [f'- {who(x["person"], x.get("place"))} → {x["answer"]["outcome"]}: '
                    + next((t['how'] for t in x['answer']['steps'] if t['stage'] == stg), '') for x in xs]
            lines += details(f'{stg} で止まった（{len(xs)} 人）', body)

        bad = [(who(x['person'], x.get('place')), check_away(x['answer'], x.get('place'), x.get('notice')))
               for x in rows]
        bad = [(w, c) for w, c in bad if c]
        if bad:
            lines += ['', f'## {r}: 答えの中の食いちがい（{len(bad)} 人）', '',
                      '数えるときは答えのまま（止まった所は段階の答えから、どうなったかは outcome から）。'
                      'くじと合わない答えだけは、どうなったかの数に入れていない。', '']
            lines += [f'- {w}: ' + '／'.join(c) for w, c in bad]
    text = '\n'.join(lines) + '\n'
    print(text)
    with open(os.path.join(HERE, 'runs', a.rounds[-1], 'tally.md'), 'w', encoding='utf-8') as f:
        f.write(text)


# ---- the away test: grouping the ideas ------------------------------------------------------------------------
IDEAS_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['groups', 'labels'],
    'properties': {
        'groups': {'type': 'array', 'minItems': 3, 'maxItems': 30, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['id', 'name', 'what'],
            'properties': {'id': {'type': 'string', 'description': 'G01, G02, ...'},
                           'name': {'type': 'string', 'description': 'サークルがすることを短く（20 字まで）'},
                           'what': {'type': 'string', 'description': 'まとめた案の中身（1〜2 文）'}}}},
        'labels': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['idea', 'group'],
            'properties': {'idea': {'type': 'string', 'description': '案の番号（P001-1 など）'},
                           'group': {'type': 'string', 'description': 'まとめの id'}}}}}}


def clashes(c):
    """The rules an idea clashes with, short: 'きまり 2', 'その他のきまり' or 'ぶつからない'. Old answers had one string."""
    out = []
    for x in c if isinstance(c, list) else [c]:
        out.append(x if x in ('ぶつからない', 'その他のきまり') or not x.split() else f'きまり {x.split()[0]}')
    return out


def write_fit(rnd, rows, out_dir):
    """Every person's fit answer per key, written before the grouping call (no call needed)."""
    fit = {k: [] for k in FIT_KEYS}
    lines = [f'# この人に届く出し方（{rnd}、作りものの人 {len(rows)} 人）', '',
             'それぞれの人の立場に立った相談役（AI）が書いた、その人に届く出し方。きまりとぶつかるものは、書いた相談役が'
             '「（きまり 3 とぶつかる）」のように書き添えている（「ぶつか」を含む答えの数は目安）。どれをとるかは人が選ぶ。'
             '並びは体のこと・言葉の順。', '']
    body = {k: [] for k in FIT_KEYS}
    for x in sorted(rows, key=lambda x: person_key(x['person'])):
        f = x['answer'].get('fit') or {}
        for k in FIT_KEYS:
            t = (f.get(k) or '').strip()
            if t:
                fit[k].append({'person': x['person']['id'], 'outcome': x['answer']['outcome'], 'text': t})
                body[k].append(f'- {who(x["person"], x.get("place"))} → {x["answer"]["outcome"]}: {t}')
    for k in FIT_KEYS:
        n_clash = sum(bool(re.search(r'ぶつか(?!らな|りませ)', e['text'])) for e in fit[k])
        lines += [f'## {k}（{len(fit[k])} 人、ぶつかると書いたもの {n_clash}）', '']
        lines += details(f'{len(fit[k])} 人の答え', body[k])
    with open(os.path.join(out_dir, 'fit.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return fit


def ideas(a):
    rows = load(a.round)
    out_dir = os.path.join(HERE, 'runs', a.round)
    fit = write_fit(a.round, rows, out_dir)
    items = []
    for x in rows:
        for i, it in enumerate(x['answer']['ideas'], 1):
            items.append(dict(it, id=f'{x["person"]["id"]}-{i}', person=x['person'], place=x.get('place'), rank=i,
                              outcome=x['answer']['outcome'], stop=first_stop(x['answer']),
                              clash=clashes(it['clash']), ok=place_ok(x)))
    listing = '\n'.join(f'{it["id"]} [{it["stage"]}] {it["idea"]}' for it in items)
    text = (f'# 案（{len(items)} 件、{len(rows)} 人）\n\n{listing}\n\n'
            'すべての案を、サークルがすることの中身でまとめてください。')
    ids = sorted(it['id'] for it in items)
    listing_sha = hashlib.sha256(listing.encode()).hexdigest()
    raw = os.path.join(out_dir, 'ideas-raw.json')
    want = {'ids': ids, 'listing_sha256': listing_sha, 'model': a.model,
            'prompt_sha256': sha_file(os.path.join(LOOK, 'ideas-prompt.md')),
            'schema_sha256': hashlib.sha256(json.dumps(IDEAS_SCHEMA, sort_keys=True).encode()).hexdigest()}
    if os.path.exists(raw) and not a.again:
        with open(raw, encoding='utf-8') as f:
            res = json.load(f)
        changed = [k for k, v in want.items() if res.get(k) != v]
        if changed:
            print(f'案か聞き方が前と変わりました（{"・".join(changed)}）。--again で聞き直してください', file=sys.stderr)
            return 1
    else:
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(IDEAS_SCHEMA, f, ensure_ascii=False)
            schema_path = f.name
        try:
            res, err = claude(user_line([{'type': 'text', 'text': text}]), os.path.join(LOOK, 'ideas-prompt.md'),
                              schema_path, a.model, a.timeout)
        finally:
            os.unlink(schema_path)
        if err:
            print(f'NG {json.dumps(err, ensure_ascii=False)[:600]}', file=sys.stderr)
            return 1
        res.update(dict(want, round=a.round, ideas=len(items), at=time.strftime('%Y-%m-%d %H:%M:%S')))
        with open(raw, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    ans = res['answer']
    gids = [g['id'] for g in ans['groups']]
    dup = sorted({g for g in gids if gids.count(g) > 1})
    if dup:
        print(f'まとめの id が重なっています（{"、".join(dup)}）。--again で聞き直してください', file=sys.stderr)
        return 1
    groups = {g['id']: g for g in ans['groups']}
    label, seen_in = {}, collections.defaultdict(set)
    for lb in ans['labels']:
        seen_in[lb['idea']].add(lb['group'])
        if lb['group'] in groups:
            label.setdefault(lb['idea'], lb['group'])
    by_id = {it['id']: it for it in items}
    unknown = sorted(set(seen_in) - set(by_id))
    multi = sorted(i for i, gs in seen_in.items() if i in by_id and len(gs & set(groups)) > 1)
    loose = [it for it in items if label.get(it['id']) not in groups]
    per = collections.defaultdict(list)
    for it in items:
        g = label.get(it['id'])
        per[g if g in groups else '—'].append(it)
    names = dict({g: groups[g]['name'] for g in groups}, **{'—': '（まとめに入らなかった案）'})
    order = sorted(per, key=lambda g: (g == '—', -len({it['person']['id'] for it in per[g]}), g))
    summary = []
    for g in order:
        its = sorted(per[g], key=lambda it: person_key(it['person']) + (it['rank'],))
        persons = list({it['person']['id']: it['person'] for it in its}.values())
        fate = {it['person']['id']: (it['outcome'], it['stop'], it['ok']) for it in its}
        summary.append({
            'group': g, 'name': names[g], 'what': groups.get(g, {}).get('what', ''), 'people': len(persons),
            'ideas': len(its), 'first': sum(it['rank'] == 1 for it in its),
            'not_away': sum(o not in AWAY for o, _, ok in fate.values() if ok),
            'off_lot': sum(not ok for _, _, ok in fate.values()),
            'stops': collections.Counter(s for _, s, ok in fate.values()
                                         if ok and s != '止まらなかった').most_common(2),
            'clash': collections.Counter(c for it in its for c in it['clash']).most_common(),
            'clashing': sum(any(c != 'ぶつからない' for c in it['clash']) for it in its),
            'stage': collections.Counter(it['stage'] for it in its).most_common(),
            'place': collections.Counter(it['place'] for it in its).most_common(),
            'not_jp': sum(lang_group(p) != '日本語が母語' for p in persons),
            'body': collections.Counter(p['body'] for p in persons if p['body'] != 'なし').most_common(3),
            'items': [{'id': it['id'], 'person': it['person']['id'], 'rank': it['rank'], 'stage': it['stage'],
                       'idea': it['idea'], 'why': it['why'], 'clash': it['clash'], 'outcome': it['outcome'],
                       'stop': it['stop'], 'ok': it['ok']} for it in its],
        })
    with open(os.path.join(out_dir, 'ideas.json'), 'w', encoding='utf-8') as f:
        json.dump({'round': a.round, 'people': len(rows), 'ideas': len(items), 'groups': summary,
                   'unknown_labels': unknown, 'not_grouped': [it['id'] for it in loose], 'multi_labels': multi,
                   'fit': fit}, f, ensure_ascii=False, indent=1)
    lines = [f'# 危ない所から離れる・近づかないための案（{a.round}、作りものの人 {len(rows)} 人、案 {len(items)} 件）', '',
             'それぞれの人の立場に立った相談役（AI）が、知っていることをすべて使って出した案。まとめは AI が 1 回で付けた'
             '目安で、案は一つも落としていない。どれをとるかは人が選ぶ。', '',
             '「きまりとぶつかる」は、出した相談役が付けた印。サークルのきまり: 1 公式の事実だけ／2 どう動くかを指図しない'
             '／3 決まった訳・公式の訳だけ／4 位置は PC だけ。この人に届く出し方は fit.md。', '',
             '「離れられなかった人」と「止まった段階」は、くじと合う答えだけを数え、くじと合わない答えの人は「+合わない」に'
             '別に書いた。', '',
             '| まとめ | 人 | 案 | 1 番に挙げた | 離れられなかった人 | 止まった段階 | 日本語が母語でない人 '
             '| きまりとぶつかる案 |', '|---|---|---|---|---|---|---|---|']
    def not_away(s):
        return f'{s["not_away"]}' + (f'（+合わない {s["off_lot"]}）' if s['off_lot'] else '')
    for s in summary:
        top = '、'.join(f'{c}（{n}）' for c, n in s['clash'] if c != 'ぶつからない')
        stops = '、'.join(f'{k} {v}' for k, v in s['stops']) or '—'
        lines.append(f'| {cell(s["name"])} | {s["people"]} | {s["ideas"]} | {s["first"]} | {not_away(s)} | {stops} '
                     f'| {s["not_jp"]} | {s["clashing"]}' + (f'：{top}' if top else '') + ' |')
    for s in summary:
        lines += ['', f'## {s["name"]}（{s["people"]} 人、案 {s["ideas"]}）', '', s['what'], '',
                  '- 段階: ' + '、'.join(f'{k} {v}' for k, v in s['stage']),
                  '- いる所: ' + '、'.join(f'{k} {v}' for k, v in s['place']),
                  '- 体のこと（なし以外、人）: ' + ('、'.join(f'{k} {v}' for k, v in s['body']) or '—'),
                  f'- 離れられなかった人: {not_away(s)}', '']
        body = []
        for e in s['items']:
            it = by_id[e['id']]
            mark = '・'.join([c for c in e['clash'] if c != 'ぶつからない'] + ([] if e['ok'] else ['くじと合わない答え']))
            body.append(f'- {e["id"]}（{traits(it["person"])}、{it["place"]} → {e["outcome"]}）[{e["stage"]}] '
                        f'{e["idea"]} — {e["why"]}' + (f' 〔{mark}〕' if mark else ''))
        lines += details(f'案 {len(body)} 件', body) if len(body) > 6 else body
    if unknown or loose or multi:
        lines += ['', f'まとめの答えに無い番号 {len(unknown)}、まとめに入らなかった案 {len(loose)}、'
                      f'二つ以上のまとめに付いた案 {len(multi)}（まとめにある、はじめのものに数えた）。']
        if multi:
            lines.append('二つ以上のまとめに付いた案: ' + '、'.join(multi))
    out = '\n'.join(lines) + '\n'
    print(out)
    with open(os.path.join(out_dir, 'ideas.md'), 'w', encoding='utf-8') as f:
        f.write(out)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--test', default='away', choices=sorted(TESTS))
    r.add_argument('--set', required=True, choices=['before', 'after', 'after3'])
    r.add_argument('--round', required=True)
    r.add_argument('--only')
    r.add_argument('--first', type=int)
    r.add_argument('-j', type=int, default=4)
    r.add_argument('--model', default='opus')
    r.add_argument('--timeout', type=int, default=900)
    t = sub.add_parser('tally')
    t.add_argument('rounds', nargs='+')
    i = sub.add_parser('ideas')
    i.add_argument('--round', required=True)
    i.add_argument('--model', default='opus')
    i.add_argument('--timeout', type=int, default=1800)
    i.add_argument('--again', action='store_true', help='ask again even if ideas-raw.json exists')
    a = ap.parse_args()
    if a.cmd == 'run':
        sys.exit(run(a))
    if a.cmd == 'ideas':
        sys.exit(ideas(a))
    if all(r.get('test') == 'away' for rnd in a.rounds for r in load(rnd)[:1]):
        tally_away(a)
    else:
        tally(a)


if __name__ == '__main__':
    main()
