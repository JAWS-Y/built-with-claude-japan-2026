"""Count the 角 (corners) the simulated people found, and compare rounds.

python3 tally.py r1            -> runs/r1/tally.md and a short summary
python3 tally.py r1 r2         -> the same for both, and r1 -> r2 side by side
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NOTICE_SCENES = (1, 2, 3, 4, 5)


def load(rnd):
    d = os.path.join(HERE, 'runs', rnd)
    out = []
    for name in sorted(os.listdir(d)):
        if name.endswith('.json') and not name.endswith('.err.json'):
            with open(os.path.join(d, name), encoding='utf-8') as f:
                out.append(json.load(f))
    return out


def group(p):
    """The group the person is counted in: the first thing that makes a notice hard to reach."""
    person = p['person']
    if person['lang'] != '日本語（母語）':
        return '日本語が母語でない'
    if person['body'] != 'なし':
        return '体のこと'
    if person['age'] < 18:
        return 'こども'
    if person['age'] >= 75:
        return '75 歳以上'
    return 'そのほか'


def stats(rows):
    corners = [c for r in rows for c in r['answer']['corners']]
    sev = collections.Counter(c['severity'] for c in corners)
    und = [s['understood'] for r in rows for s in r['answer']['scenes'] if s['scene'] in NOTICE_SCENES]
    missed = [r for r in rows
              if any(s['understood'] <= 1 or not s['reached'] for s in r['answer']['scenes'] if s['scene'] in NOTICE_SCENES)]
    setup_ok = sum(1 for r in rows for s in r['answer']['scenes'] if s['scene'] == 0 and s['reached'])
    return {
        'people': len(rows),
        'corners': len(corners),
        'sev3': sev.get(3, 0), 'sev2': sev.get(2, 0), 'sev1': sev.get(1, 0),
        'understood_avg': round(sum(und) / len(und), 2) if und else 0,
        'missed_people': len(missed),
        'setup_ok': setup_ok,
        'cost_usd': round(sum(r.get('cost_usd') or 0 for r in rows), 2),
    }, corners


def report(rnd):
    rows = load(rnd)
    st, corners = stats(rows)
    L = [f'# 角の数え上げ: {rnd}', '',
         f'演じた人 {st["people"]} 人（作りものの人で、実在の利用者ではありません）。', '',
         f'- 角 {st["corners"]}（重い 3: {st["sev3"]} / 2: {st["sev2"]} / 1: {st["sev1"]}）',
         f'- 知らせ（場面 1〜5）の分かり方の平均 {st["understood_avg"]} / 3',
         f'- 知らせのどれかで「危ないらしい」までしか分からなかった、または届かなかった人 {st["missed_people"]} 人',
         f'- はじめの設定ができた人 {st["setup_ok"]} 人', '']
    for title, key in (('どこの角か', 'where'), ('どんな角か', 'kind'), ('どの場面か', 'scene')):
        cnt = collections.Counter(c[key] for c in corners)
        heavy = collections.Counter(c[key] for c in corners if c['severity'] == 3)
        L.append(f'## {title}')
        L += [f'- {k}: {n}（重い {heavy.get(k, 0)}）' for k, n in cnt.most_common()]
        L.append('')
    L.append('## 人の分け方ごと')
    by = collections.defaultdict(list)
    for r in rows:
        by[group(r)].append(r)
    for g, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        s, _ = stats(rs)
        L.append(f'- {g}（{s["people"]} 人）: 角 {s["corners"]}、重い {s["sev3"]}、分かり方 {s["understood_avg"]}、'
                 f'届かなかった人 {s["missed_people"]}')
    L.append('')
    L.append('## よく出た文字（quote）')
    q = collections.Counter((c['where'], c['quote'].strip()) for c in corners if c['quote'].strip())
    for (w, text), n in q.most_common(40):
        L.append(f'- {n} 回 [{w}] {text[:80]}')
    L.append('')
    L.append('## 重い角（severity 3）')
    for r in rows:
        for c in r['answer']['corners']:
            if c['severity'] == 3:
                p = r['person']
                L.append(f'- {p["id"]}（{p["age"]} 歳・{p["lang"]}・{p["reading"]}・{p["body"]}・{p["situation"]}）'
                         f' 場面 {c["scene"]} [{c["where"]}/{c["kind"]}] {c["why"]} → {c["fix_hint"]}')
    L.append('')
    L.append('## いまのサークルに無い働き（角には数えない）')
    o = collections.Counter(x for r in rows for x in r['answer']['outside'])
    L += [f'- {x}' for x, _ in o.most_common(30)]
    path = os.path.join(HERE, 'runs', rnd, 'tally.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    return st, path


def main():
    rounds = sys.argv[1:]
    if not rounds:
        raise SystemExit(__doc__)
    res = [(r, *report(r)) for r in rounds]
    for r, st, path in res:
        print(r, json.dumps(st, ensure_ascii=False), '->', os.path.relpath(path, HERE))
    if len(res) == 2:
        (a, sa, _), (b, sb, _) = res
        print(f'{a} -> {b}')
        for k in ('corners', 'sev3', 'sev2', 'sev1', 'understood_avg', 'missed_people', 'setup_ok'):
            print(f'  {k}: {sa[k]} -> {sb[k]}')


if __name__ == '__main__':
    main()
