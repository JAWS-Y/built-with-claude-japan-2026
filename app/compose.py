"""Claude が知らせの文を組み立てる。

Claude に渡すのは build_notice() が集めた公式の事実（文と id）だけで、出典は渡さない。
Claude は事実をつないで読みやすい文にし、どの文にどの事実を使ったかを id で返す。
アプリはその返事を 1 文ずつ確かめる（全部の事実が使われているか、事実にない数字・名前・言い回しが無いか、
どう動くか・安全・予想を書いていないか）。1 つでも外れたら Claude の文は使わず、事実を並べただけの文に戻す。
出典は、文ごとに使った事実の出典をアプリが付け直す。
試しの作りものの警報（出典「試し」）は、本物の事実と同じ文に入れさせない。混ざると本物の数まで「試し」の印が付くため。

ANTHROPIC_API_KEY が無いとき・IMAKOKO_CLAUDE=off のとき・anthropic パッケージが無いときは何もしない。
"""

import hashlib
import json
import os
import re
import sys
import threading
import unicodedata

MODEL = os.environ.get('IMAKOKO_CLAUDE_MODEL', 'claude-opus-5-5')
EFFORT = 'medium'
LINE_MAX = 160
CACHE_MAX = 64

SYSTEM = """あなたは、いまいる場所の公式の防災情報を、携帯の通知で読む人向けの短い文にまとめます。
渡すのは公式の事実の一覧（id と文）です。次を必ず守ってください。

- 使ってよいのは渡した事実だけです。事実にない数字・名前・地名・時刻・理由を足さないでください。
- 渡した事実はどれも、どこかの文で必ず使ってください。数字と名前は事実に書いてあるとおりに書き、数字は算用数字のままにします。
- 事実をつないだり、順を変えたりしてかまいません。警報の事実があるときは、最初の文で、出ている警報とこの場所の性質を結びつけてください。
- ただし id が demo の事実（試しの作りものの警報）は、ほかの事実と同じ文に入れず、「試し」の言葉を残して、それだけで最初の 1 文にしてください。この場所の性質は次の文に書きます。ほかの文には「試し」「作りもの」と書かないでください。
- どう動けばよいか（逃げる・控える・備えるなど）は書かないでください。
- 「安全」「大丈夫」「起きない」のように、無いこと・心配いらないことは書かないでください。
- 事実にない見込みや予想は書かないでください。
- 出典は書かないでください（アプリが付けます）。事実にあるもののほかに括弧を使わないでください。
- です・ます の言い切りで、1 文は 120 字くらいまでにします。
- 文ごとに、その文で使った事実の id を facts に並べてください。"""

SCHEMA = {
    'type': 'object',
    'properties': {
        'lines': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'text': {'type': 'string'},
                    'facts': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['text', 'facts'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['lines'],
    'additionalProperties': False,
}

# どう動くか・無いこと・予想。事実の文に入っていない限り、Claude の文には出てはいけない
BANNED = {
    'act': ['避難', '逃げ', 'ください', 'しましょう', 'すべき', '控え', '急い', '備え', '準備', '注意して',
            '気をつけ', '気を付け', '移動', '近づか', '向かっ', '早め', 'すぐ', '命を'],
    'safe': ['安全', '大丈夫', '心配', '問題', 'ない', 'ません', 'なし'],
    'guess': ['予想', '予測', '見込', 'おそれ', '恐れ', 'かもしれ', '可能性', 'でしょう', 'だろう', '危な', '危険'],
}
DEMO_SOURCE = '試し'               # app.build_notice が作りものの警報に付ける出典
DEMO_WORDS = ['試し', '作りもの']  # 試しの文のほかには出てはいけない
KANJI_NUM = re.compile(r'[〇一二三四五六七八九十百千万]+\s*(?:か所|ヶ所|箇所|カ所|km|キロ|分|時|m|mm|ミリ|メートル|件|基|段階|倍)')
NUMBER = re.compile(r'\d+(?:\.\d+)?')
RANGE = re.compile(r'\d+(?:\.\d+)?\s*[～〜~\-－]\s*\d+(?:\.\d+)?')


class ComposeError(Exception):
    def __init__(self, msg, retry=False):
        super().__init__(msg)
        self.retry = retry


def _norm(text):
    return unicodedata.normalize('NFKC', text)


def _numbers(text):
    out = set()
    for n in NUMBER.findall(_norm(text)):
        if '.' in n:
            n = n.rstrip('0').rstrip('.')
        out.add(n.lstrip('0') or '0')
    return out


def _range(text):
    return tuple(NUMBER.findall(text))


def _sources(cited):
    srcs = list(dict.fromkeys(f['source'] for f in cited))
    # 「気象庁」と「気象庁 11/03 12:00」が両方あれば、時刻のある方だけ残す
    return [s for s in srcs if not any(o != s and o.startswith(s + ' ') for o in srcs)]


def validate(facts, result):
    """Check Claude's lines against the facts. Return the final lines (with sources) or raise ComposeError."""
    by_id = {f['id']: f for f in facts}
    lines = (result or {}).get('lines')
    if not isinstance(lines, list) or not lines:
        raise ComposeError('文がありません')
    # 文は、事実にある文の数まで（クマの記録のように 1 つの事実が 2 文なら、2 文に分けてよい）
    if len(lines) > sum(len([x for x in f['text'].split('。') if x.strip()]) for f in facts):
        raise ComposeError('事実より文が多い')
    used, out = set(), []
    for i, line in enumerate(lines, 1):
        text = (line.get('text') or '').strip() if isinstance(line, dict) else ''
        ids = line.get('facts') if isinstance(line, dict) else None
        if not text:
            raise ComposeError(f'{i} 文目が空')
        if len(text) > LINE_MAX:
            raise ComposeError(f'{i} 文目が長すぎる')
        if not isinstance(ids, list) or not ids:
            raise ComposeError(f'{i} 文目に事実の id がない')
        unknown = [x for x in ids if x not in by_id]
        if unknown:
            raise ComposeError(f'{i} 文目に知らない id: {unknown}')
        cited = [by_id[x] for x in dict.fromkeys(ids)]
        used.update(f['id'] for f in cited)
        demo = [f for f in cited if f['source'] == DEMO_SOURCE]
        if demo and len(demo) < len(cited):
            raise ComposeError(f'{i} 文目に試しの事実と本物の事実が混ざっている')
        if demo and DEMO_SOURCE not in text:
            raise ComposeError(f'{i} 文目は試しの事実なのに「{DEMO_SOURCE}」と書いていない')
        if not demo:
            said = [w for w in DEMO_WORDS if w in text]
            if said:
                raise ComposeError(f'{i} 文目は本物の事実なのに {said} と書いている')
        src_text = _norm(''.join(f['text'] for f in cited))
        extra = _numbers(text) - _numbers(src_text)
        if extra:
            raise ComposeError(f'{i} 文目に事実にない数字: {sorted(extra)}')
        ranges = [m for m in RANGE.findall(_norm(text)) if _range(m) not in {_range(r) for r in RANGE.findall(src_text)}]
        if ranges:
            raise ComposeError(f'{i} 文目に事実にない数字の幅: {ranges}')
        kanji = [m for m in KANJI_NUM.findall(_norm(text)) if m not in src_text]
        if kanji:
            raise ComposeError(f'{i} 文目に漢数字の数: {kanji}')
        for quoted in re.findall(r'「([^」]*)」', text) + re.findall(r'[（(]([^）)]*)[）)]', _norm(text)):
            if _norm(quoted) not in src_text:
                raise ComposeError(f'{i} 文目に事実にない「{quoted}」')
        for kind, words in BANNED.items():
            bad = [w for w in words if w in text and w not in src_text]
            if bad:
                raise ComposeError(f'{i} 文目に書かない言葉（{kind}）: {bad}')
        # 試しの文は自分で「試し」と言うので、出典「（試し）」は付けない（試しの印は 1 か所）
        out.append(text.rstrip('。') + ('' if demo else '（' + '・'.join(_sources(cited)) + '）') + '。')
    missing = [f['id'] for f in facts if f['id'] not in used]
    if missing:
        raise ComposeError(f'使われていない事実: {missing}')
    return out


GUIDE = 'guide'


def for_claude(facts):
    """The facts Claude writes from. The guide (「〜に警戒してください」) is the app's own sentence in the
    気象庁 form: Claude never rewrites it, the app puts it in as it is."""
    return [f for f in facts if f['id'] != GUIDE]


def with_guide(lines, facts):
    """Claude's lines with the app's guide sentence second, as in the plain body (head, then what to do)."""
    guide = next((f for f in facts if f['id'] == GUIDE), None)
    if not guide:
        return lines
    return lines[:1] + [guide['text'].rstrip('。') + '（' + guide['source'] + '）。'] + lines[1:]


class ClaudeClient:
    """One call per notice to the Claude API (official anthropic SDK)."""

    def __init__(self, model=MODEL):
        import anthropic  # only here: the rest of the app stays stdlib-only
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(timeout=60.0, max_retries=2)  # key from ANTHROPIC_API_KEY
        self.model = model

    def write(self, facts):
        a = self.anthropic
        content = '事実:\n' + json.dumps([{'id': f['id'], 'text': f['text']} for f in facts], ensure_ascii=False)
        try:
            r = self.client.beta.messages.create(
                model=self.model,
                max_tokens=8000,
                system=SYSTEM,
                messages=[{'role': 'user', 'content': content}],
                thinking={'type': 'adaptive'},
                output_config={'effort': EFFORT, 'format': {'type': 'json_schema', 'schema': SCHEMA}},
                betas=['server-side-fallback-2026-07-01'],
                fallbacks='default',
            )
        except a.BadRequestError as e:
            raise ComposeError(f'要求の形が違う（400）: {e.message}')
        except a.AuthenticationError:
            raise ComposeError('API キーが通らない（401）')
        except a.PermissionDeniedError:
            raise ComposeError('このキーでは使えない（403）')
        except a.NotFoundError:
            raise ComposeError(f'モデル {self.model} が見つからない（404）')
        except a.RateLimitError as e:
            raise ComposeError(f"混んでいる（429、retry-after {e.response.headers.get('retry-after')}）", retry=True)
        except a.APIStatusError as e:
            raise ComposeError(f'API のエラー（{e.status_code}）', retry=e.status_code >= 500)
        except a.APIConnectionError:
            raise ComposeError('API につながらない・時間切れ', retry=True)
        if r.stop_reason == 'refusal':
            category = getattr(getattr(r, 'stop_details', None), 'category', None)
            raise ComposeError(f'Claude が断った（{category}）')
        if r.stop_reason != 'end_turn':
            raise ComposeError(f'途中で止まった（{r.stop_reason}）')
        text = next((b.text for b in r.content if b.type == 'text'), None)
        if text is None:
            raise ComposeError('返事に文が無い')
        try:
            data = json.loads(text)
        except ValueError:
            raise ComposeError('返事が JSON でない')
        return {**data, 'model': r.model, 'request_id': getattr(r, '_request_id', None)}


class FakeClient:
    """For tests: returns `result` (or raises it) without calling the API."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def write(self, facts):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)


_warned = False


def client_from_env():
    """A ClaudeClient when ANTHROPIC_API_KEY is set and the SDK is installed, else None."""
    global _warned
    if not os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('IMAKOKO_CLAUDE') == 'off':
        return None
    try:
        return ClaudeClient()
    except ImportError:
        if not _warned:
            print('anthropic パッケージが無いので、Claude の文の組み立ては使いません（pip install -U anthropic）',
                  file=sys.stderr)
            _warned = True
        return None


def facts_key(notice):
    data = json.dumps([notice.get('notes'), notice.get('facts')], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()[:32]


_cache = {}
_lock = threading.Lock()
_busy = {}   # facts key -> lock, so two pages asking for the same notice make one call


def _remember(key, value):
    with _lock:
        _cache[key] = value
        while len(_cache) > CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        for k in [k for k, b in _busy.items() if k not in _cache and not b.locked()]:
            _busy.pop(k)


def cached(notice):
    """The composed notice for these facts if it was made before, else None."""
    with _lock:
        hit = _cache.get(facts_key(notice))
    return _apply(notice, hit) if hit else None


def _apply(notice, done):
    out = dict(notice, plain_body=notice['body'], composer=done['composer'])
    if done['composer']['ok']:
        # notes は素の本文の試しの頭（「本物は「道路」の行」）。Claude の文では試しの文が自分で言うので足さない
        out['body'] = '\n'.join(done['lines'])
    return out


def compose(notice, client):
    """Return the notice with a Claude-written body when it passes the checks, else with the plain body."""
    facts = for_claude(notice.get('facts') or [])
    if not notice.get('notify') or client is None or not facts:
        return notice
    key = facts_key(notice)
    with _lock:
        busy = _busy.setdefault(key, threading.Lock())
    with busy:
        hit = cached(notice)
        if hit:
            return hit
        try:
            result = client.write(facts)
            lines = with_guide(validate(facts, result), notice['facts'])
            done = {'lines': lines, 'composer': {'ok': True, 'by': result.get('model') or MODEL,
                                                 'request_id': result.get('request_id')}}
        except ComposeError as e:
            done = {'lines': None, 'composer': {'ok': False, 'reason': str(e)}}
            if e.retry:
                return _apply(notice, done)
        _remember(key, done)
        return _apply(notice, done)
