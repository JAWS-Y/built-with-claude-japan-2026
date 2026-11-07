"""Simulated people for the persona test: a fixed, seeded mix of age, language, body and situation.

python3 personas.py [-n 100] [--seed 2026]  -> personas.json
Every value is used at least once (for n large enough). Each pick takes the least used value that fits the
person so far, so the mix stays even. These are simulated people, not real users.
"""
import argparse
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))

AGES = [9, 14, 17, 22, 28, 35, 43, 51, 60, 68, 75, 83, 91]
# (language, weight). 日本語を母語とする人を多めに、ほかの言葉は同じくらいずつ
LANGS = [('日本語（母語）', 8), ('ベトナム語', 1), ('ネパール語', 1), ('ポルトガル語（ブラジル）', 1), ('中国語', 1),
         ('タガログ語', 1), ('英語', 1), ('インドネシア語', 1), ('韓国語', 1), ('ミャンマー語', 1), ('スペイン語', 1),
         ('日本語（母語）だが漢字が苦手', 1)]
READING = ['日本語はひらがなまで読める', 'ひらがなとカタカナは読める', '漢字も少し読める', '日本語はほとんど読めない']
BODIES = [('なし', 5), ('全盲（TalkBack で読み上げ）', 1), ('弱視（文字を大きくしている）', 1), ('色の見分けがつきにくい（P 型）', 1),
          ('ろう（手話が第一の言葉）', 1), ('難聴（音が聞こえにくい）', 1), ('軽い知的障害', 1), ('ADHD', 1),
          ('自閉スペクトラム', 1), ('認知症のはじめ', 1), ('ディスレクシア（読み書きの障害）', 1), ('手のふるえ', 1),
          ('車いす', 1)]
SITUATIONS = ['一人暮らし', '小さい子を連れている', '親の介護をしている', '夜勤明けで寝ている', '車を運転している',
              '旅行で来ている', '引っ越してきたばかり', '外で働いている（工事・配達など）', '犬と散歩している', '妊娠中',
              'スマホに慣れていない', '古い携帯を使っている', '山あいに住んでいる', '都心の駅の近くにいる', '学校の帰り']

CHILD = 18


def fits(p, key, value):
    age = p.get('age')
    if key == 'situation':
        if value == '車を運転している' and (age < CHILD or age > 83 or p['body'].startswith(('全盲', '弱視', '認知症'))):
            return False
        if value == '妊娠中' and not 22 <= age <= 43:
            return False
        if value == '学校の帰り' and age >= CHILD:
            return False
        if value in ('親の介護をしている', '夜勤明けで寝ている', '外で働いている（工事・配達など）', '小さい子を連れている') \
                and age < CHILD:
            return False
    if key == 'body' and value == '認知症のはじめ' and age < 60:
        return False
    return True


def pick(rng, used, options, p, key):
    """The least used value (by weight) that fits the person; ties are broken at random."""
    ok = [(v, w) for v, w in options if fits(p, key, v)]
    best = min(used.get(v, 0) / w for v, w in ok)
    return rng.choice([v for v, w in ok if used.get(v, 0) / w == best])


def make(n=100, seed=2026):
    rng = random.Random(seed)
    used = {k: {} for k in ('age', 'lang', 'reading', 'body', 'situation')}
    out = []
    for i in range(n):
        p = {'id': f'P{i + 1:03d}'}
        for key, options in (('age', [(a, 1) for a in AGES]), ('lang', LANGS), ('body', BODIES),
                             ('situation', [(s, 1) for s in SITUATIONS])):
            p[key] = pick(rng, used[key], options, p, key)
            used[key][p[key]] = used[key].get(p[key], 0) + 1
        if p['lang'].startswith('日本語'):
            p['reading'] = '漢字が苦手（ひらがなは読める）' if '漢字' in p['lang'] else '日本語はふつうに読める'
            p['lang'] = '日本語（母語）'
        else:
            p['reading'] = pick(rng, used['reading'], [(r, 1) for r in READING], p, 'reading')
            used['reading'][p['reading']] = used['reading'].get(p['reading'], 0) + 1
        if p['age'] <= 9 and p['reading'] == '日本語はふつうに読める':
            p['reading'] = '小学校で習った漢字まで読める'
        out.append(p)
    return out


def describe(p):
    return '\n'.join([f'- 年齢: {p["age"]} 歳', f'- 母語: {p["lang"]}', f'- 日本語の読み: {p["reading"]}',
                      f'- 体のこと: {p["body"]}', f'- 暮らし・そのとき: {p["situation"]}', '- 携帯: Android'])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-n', type=int, default=100)
    ap.add_argument('--seed', type=int, default=2026)
    a = ap.parse_args()
    people = make(a.n, a.seed)
    with open(os.path.join(HERE, 'personas.json'), 'w', encoding='utf-8') as f:
        json.dump(people, f, ensure_ascii=False, indent=1)
    for key in ('age', 'lang', 'reading', 'body', 'situation'):
        counts = {}
        for p in people:
            counts[p[key]] = counts.get(p[key], 0) + 1
        print(key, dict(sorted(counts.items(), key=lambda kv: -kv[1])))


if __name__ == '__main__':
    main()
