"""Look round: each simulated person sees 4 phone screenshots of サークル and lists the 角 (corners) they hit.

The text rounds (run.py) describe the screens in words, so they never see the look: a cut title, a word broken
across two lines, an old position, a page named twice. This round shows the real screens instead.

python3 look.py run --set before --round look-before [--first 5] [-j 4] [--model opus]
python3 look.py run --set after  --round look-after
python3 look.py tally look-before look-after      -> prints the counts, writes runs/<last>/tally.md

Images: look/shots/<set>-1-collapsed.png, -2-expanded.png, -3-app.png, -4-app-down.png (emulator, 1080x2400).
One `claude -p` call per person with the images inline (stream-json input), no tools, no MCP, no settings and no
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


def message(person, shots):
    content = [{'type': 'text', 'text': '\n'.join(['# あなたが演じる人', personas.describe(person), '',
                                                   '# 画面の写し（4 枚）'])}]
    for label, data in shots:
        content.append({'type': 'text', 'text': label})
        content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                                    'data': base64.b64encode(data).decode()}})
    content.append({'type': 'text', 'text': 'この人として、画面 1 から 4 までを見て答えてください。'})
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}}, ensure_ascii=False)


def ask(person, shots, model, timeout):
    with open(os.path.join(LOOK, 'schema.json'), encoding='utf-8') as f:
        schema = json.dumps(json.load(f), ensure_ascii=False)
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
    cmd = ['claude', '-p', '--model', model, '--tools', '', '--strict-mcp-config', '--setting-sources', '',
           '--no-session-persistence', '--disable-slash-commands', '--input-format', 'stream-json',
           '--output-format', 'stream-json', '--verbose', '--json-schema', schema,
           '--system-prompt-file', os.path.join(LOOK, 'prompt.md')]
    with tempfile.TemporaryDirectory() as cwd:
        t0 = time.time()
        p = subprocess.run(cmd, input=message(person, shots) + '\n', capture_output=True, text=True, cwd=cwd,
                           env=env, timeout=timeout)
        took = round(time.time() - t0, 1)
    out = None
    for line in p.stdout.splitlines():
        try:
            m = json.loads(line)
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


def run(a):
    with open(os.path.join(HERE, 'personas.json'), encoding='utf-8') as f:
        people = json.load(f)
    shots = images(a.set)
    shots_sha = hashlib.sha256(b''.join(d for _, d in shots)).hexdigest()
    if a.only:
        want = set(a.only.split(','))
        people = [p for p in people if p['id'] in want]
    if a.first:
        people = people[:a.first]
    out_dir = os.path.join(HERE, 'runs', a.round)
    os.makedirs(out_dir, exist_ok=True)
    todo = [p for p in people if not os.path.exists(os.path.join(out_dir, p['id'] + '.json'))]
    print(f'{a.round} ({a.set}): {len(todo)} 人（済み {len(people) - len(todo)}）, model {a.model}, -j {a.j}',
          file=sys.stderr)

    def one(p):
        try:
            res, err = ask(p, shots, a.model, a.timeout)
        except subprocess.TimeoutExpired:
            res, err = None, {'timeout': a.timeout}
        if err:
            with open(os.path.join(out_dir, p['id'] + '.err.json'), 'w', encoding='utf-8') as f:
                json.dump(err, f, ensure_ascii=False, indent=1)
            return p['id'], None, err
        res.update({'person': p, 'model': a.model, 'set': a.set, 'shots_sha256': shots_sha,
                    'at': time.strftime('%Y-%m-%d %H:%M:%S')})
        with open(os.path.join(out_dir, p['id'] + '.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        return p['id'], res, None

    total, failed = 0.0, 0
    with cf.ThreadPoolExecutor(max_workers=a.j) as ex:
        for pid, res, err in ex.map(one, todo):
            if err:
                failed += 1
                print(f'{pid}: NG {json.dumps(err, ensure_ascii=False)[:300]}', file=sys.stderr)
                continue
            total += res.get('cost_usd') or 0
            print(f'{pid}: 角 {len(res["answer"]["corners"])}, {res["took"]}s, ${res.get("cost_usd") or 0:.3f}',
                  file=sys.stderr)
    print(f'done: {len(todo) - failed} ok, {failed} NG, 計 ${total:.2f}（Max では使った量の目安）', file=sys.stderr)
    return 1 if failed else 0


# The four look corners found on the emulator (11-07) and fixed in e251a67. The kind alone is not enough: after the
# fix the cut and the line break moved to another place, so each row matches what is cut or split (quote and why).
LEVEL_CUT = re.compile(r'レベ…|レベルの数字|レベルいくつ|数字が見えない|「3」が切れ|3」が切れ|数字の3が')
MID_WORD = re.compile(r'初\s*[\n／/ ]\s*台|「初」と「台」|みず\s*[\n／/]\s*が|「みず」と「が」')
PAGE_NAME = re.compile(r'ボタン|ページの名|リンク|（気象庁）|洪水予報')


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
          lambda c: c['kind'] == '同じものが何度も' and not PAGE_NAME.search(_text(c)))]


def load(rnd):
    d = os.path.join(HERE, 'runs', rnd)
    out = []
    for n in sorted(os.listdir(d)):
        if n.endswith('.json') and not n.endswith('.err.json'):
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
        'quotes': collections.Counter(c['quote'].strip() for c in corners if c['quote'].strip() and c['severity'] >= 2),
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
    lines += ['', '## 直した 4 つの角と、その残り（角の数 / 挙げた人）', '', '| | ' + ' | '.join(r for r, _ in st) + ' |',
              '|---|' + '---|' * len(st)]
    for i, (name, _) in enumerate(FIXED):
        row(name, lambda s, i=i: f"{s['fixed'][i][1]} / {s['fixed'][i][2]} 人")
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--set', required=True, choices=['before', 'after'])
    r.add_argument('--round', required=True)
    r.add_argument('--only')
    r.add_argument('--first', type=int)
    r.add_argument('-j', type=int, default=4)
    r.add_argument('--model', default='opus')
    r.add_argument('--timeout', type=int, default=900)
    t = sub.add_parser('tally')
    t.add_argument('rounds', nargs='+')
    a = ap.parse_args()
    if a.cmd == 'run':
        sys.exit(run(a))
    tally(a)


if __name__ == '__main__':
    main()
