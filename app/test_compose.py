"""Tests for compose.py (Claude writes the notice text; the app checks it). No network, no API key:
python3 -m unittest test_compose"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

import app
import compose
import phone
from test_app import AREA, KIKI, LORE, RAIN, ROAD, ZONE_ITEMS, warn
from test_phone import LAT, LON, FakeGoogle, out_for


def landslide():
    return app.build_notice(AREA, warn(('49', 'landslide', 4)), ZONE_ITEMS, KIKI, RAIN, LORE)


# what Claude might send back for landslide(): every fact used, nothing added
GOOD = {'lines': [
    {'text': '千代田区にレベル4土砂災害危険警報が出ていて、いる場所は土砂災害警戒区域（土石流）に入っています。',
     'facts': ['warning', 'zone']},
    {'text': '土砂キキクルはこの地点で「危険（紫）警戒レベル４相当」です。', 'facts': ['kiki-land']},
    {'text': '降水ナウキャストでは、この地点はいま 30～50 mm/h、60分後は 50～80 mm/h です。', 'facts': ['rain']},
    {'text': '2.5km 先に土砂災害の伝承碑「B」があります。', 'facts': ['lore']}],
    'model': 'claude-opus-5-5', 'request_id': 'req_test'}


def with_line(i, text=None, facts=None):
    """GOOD with line i changed."""
    lines = [dict(x) for x in GOOD['lines']]
    if text is not None:
        lines[i]['text'] = text
    if facts is not None:
        lines[i]['facts'] = facts
    return {**GOOD, 'lines': lines}


class ComposeTest(unittest.TestCase):
    def setUp(self):
        compose._cache.clear()

    def compose(self, result, notice=None):
        n = notice or landslide()
        c = compose.FakeClient(result)
        return n, compose.compose(n, c), c

    def assertPlain(self, result, reason):
        n, out, _ = self.compose(result)
        self.assertFalse(out['composer']['ok'])
        self.assertIn(reason, out['composer']['reason'])
        self.assertEqual(out['body'], n['body'])
        return out

    def test_good_lines_get_their_sources_back(self):
        n, out, c = self.compose(GOOD)
        self.assertEqual(out['composer'], {'ok': True, 'by': 'claude-opus-5-5', 'request_id': 'req_test'})
        self.assertEqual(out['body'].split('\n'), [
            '千代田区にレベル4土砂災害危険警報が出ていて、いる場所は土砂災害警戒区域（土石流）に入っています'
            '（気象庁・ハザードマップポータル）。',
            '土砂キキクルはこの地点で「危険（紫）警戒レベル４相当」です（気象庁 11/03 12:00）。',
            '降水ナウキャストでは、この地点はいま 30～50 mm/h、60分後は 50～80 mm/h です（気象庁）。',
            '2.5km 先に土砂災害の伝承碑「B」があります（国土地理院）。'])
        self.assertEqual(out['plain_body'], n['body'])
        self.assertEqual(out['key'], n['key'])
        self.assertEqual(c.calls, 1)

    def test_plain_notice_is_not_changed(self):
        n = landslide()
        before = json.dumps(n, ensure_ascii=False, sort_keys=True)
        compose.compose(n, compose.FakeClient(GOOD))
        self.assertEqual(json.dumps(n, ensure_ascii=False, sort_keys=True), before)

    def test_same_source_with_time_wins(self):
        out = compose.validate(landslide()['facts'], {'lines': [
            {'text': '千代田区にレベル4土砂災害危険警報が出ていて、土砂キキクルは「危険（紫）警戒レベル４相当」です。',
             'facts': ['warning', 'kiki-land']},
            {'text': 'いる場所は土砂災害警戒区域（土石流）に入っています。', 'facts': ['zone']},
            {'text': 'この地点はいま 30～50 mm/h、60分後 50～80 mm/h です。', 'facts': ['rain']},
            {'text': '2.5km 先に土砂災害の伝承碑「B」があります。', 'facts': ['lore']}]})
        self.assertTrue(out[0].endswith('（気象庁 11/03 12:00）。'))

    def test_missing_fact(self):
        self.assertPlain({'lines': GOOD['lines'][:3]}, '使われていない事実')

    def test_number_not_in_facts(self):
        self.assertPlain(with_line(2, 'この地点はいま 30～60 mm/h、60分後は 50～80 mm/h です。'), '事実にない数字の幅')

    def test_number_from_another_fact(self):
        # 2.5 is in the lore fact, but this line cites only the rain fact
        self.assertPlain(with_line(2, 'この地点はいま 30～50 mm/h、60分後は 50～80 mm/h、2.5km 先も同じです。'),
                         '事実にない数字')

    def test_kanji_number(self):
        n = app.build_notice(AREA, warn(('03', 'rain', 3)), [], [], None, [], ROAD)
        result = {'lines': [
            {'text': '千代田区にレベル３大雨警報が出ています。', 'facts': ['warning']},
            {'text': '3km 以内に、国が道路冠水想定箇所として挙げている所が七か所あり、'
                     'いちばん近いのは 0.4km 先の「〇〇アンダーパス」です。', 'facts': ['road']}]}
        out = compose.compose(n, compose.FakeClient(result))
        self.assertFalse(out['composer']['ok'])
        self.assertIn('漢数字', out['composer']['reason'])
        result['lines'][1]['text'] = result['lines'][1]['text'].replace('七か所', '7 か所')
        compose._cache.clear()
        self.assertTrue(compose.compose(n, compose.FakeClient(result))['composer']['ok'])

    def test_name_not_in_facts(self):
        self.assertPlain(with_line(3, '2.5km 先に土砂災害の伝承碑「C」があります。'), '事実にない「C」')
        compose._cache.clear()
        self.assertPlain(with_line(1, '土砂キキクルはこの地点で危険（赤）です。'), '事実にない「赤」')

    def test_how_to_act(self):
        for text in ('2.5km 先に土砂災害の伝承碑「B」があります。すぐに避難してください。',
                     '2.5km 先に土砂災害の伝承碑「B」があるので、早めに備えましょう。'):
            compose._cache.clear()
            self.assertPlain(with_line(3, text), '書かない言葉（act）')

    def test_safe_and_guess(self):
        self.assertPlain(with_line(3, '2.5km 先に土砂災害の伝承碑「B」がありますが、大丈夫です。'), '（safe）')
        compose._cache.clear()
        self.assertPlain(with_line(3, '2.5km 先に土砂災害の伝承碑「B」があり、崩れるおそれがあります。'), '（guess）')

    def test_word_in_the_fact_is_allowed(self):
        # 「危険」 is in the kiki fact itself, so the line citing it may use it
        n, out, _ = self.compose(with_line(1, '土砂キキクルはこの地点で「危険（紫）警戒レベル４相当」、危険です。'))
        self.assertTrue(out['composer']['ok'])

    def test_unknown_or_missing_ids(self):
        self.assertPlain(with_line(3, facts=['lore', 'x']), '知らない id')
        compose._cache.clear()
        self.assertPlain(with_line(3, facts=[]), 'id がない')

    def test_bad_shapes(self):
        for result, reason in (({}, '文がありません'), ({'lines': []}, '文がありません'),
                               ({'lines': [{'text': '', 'facts': ['warning']}]}, '空'),
                               (with_line(3, 'あ' * 200), '長すぎる'),
                               ({'lines': GOOD['lines'] * 2}, '事実より文が多い')):
            compose._cache.clear()
            self.assertPlain(result, reason)

    def test_refusal_is_plain_and_remembered(self):
        n = landslide()
        c = compose.FakeClient(compose.ComposeError('Claude が断った（cyber）'))
        out = compose.compose(n, c)
        self.assertEqual(out['composer'], {'ok': False, 'reason': 'Claude が断った（cyber）'})
        self.assertEqual(out['body'], n['body'])
        compose.compose(n, c)
        self.assertEqual(c.calls, 1)

    def test_busy_is_tried_again(self):
        n = landslide()
        c = compose.FakeClient(compose.ComposeError('混んでいる（429）', retry=True))
        compose.compose(n, c)
        compose.compose(n, c)
        self.assertEqual(c.calls, 2)

    def test_same_facts_one_call(self):
        n, _, c = self.compose(GOOD)
        self.assertTrue(compose.compose(landslide(), c)['composer']['ok'])
        self.assertEqual(c.calls, 1)
        self.assertTrue(compose.cached(landslide())['composer']['ok'])

    def test_nothing_to_do(self):
        c = compose.FakeClient(GOOD)
        n = landslide()
        self.assertIs(compose.compose(n, None), n)
        quiet = app.build_notice(AREA, warn(('10', 'rain', 2)), ZONE_ITEMS, KIKI, RAIN, LORE)
        self.assertIs(compose.compose(quiet, c), quiet)
        self.assertEqual(c.calls, 0)

    def test_demo_note_stays_last(self):
        w = app.add_demo({'warnings': [], 'flood': []}, ('landslide', 4), app.datetime(2026, 11, 3, tzinfo=app.JST))
        n = app.build_notice(AREA, w, [], [], None, [])
        self.assertEqual([(f['id'], f['plain'], f['source']) for f in n['facts']], [('demo', False, '試し')])
        out = compose.compose(n, compose.FakeClient({'lines': [
            {'text': '千代田区にレベル４土砂災害危険警報が出ています（これは試しの作りもので、気象庁は出していません）。',
             'facts': ['demo']}]}))
        self.assertTrue(out['composer']['ok'], out['composer'])
        self.assertEqual(out['body'].split('\n'), [
            '千代田区にレベル４土砂災害危険警報が出ています（これは試しの作りもので、気象庁は出していません）（試し）。'] + n['notes'])

    def test_client_from_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(compose.client_from_env())
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'x', 'IMAKOKO_CLAUDE': 'off'}, clear=True):
            self.assertIsNone(compose.client_from_env())

    def test_no_instructions_in_the_prompt_output_rules(self):
        # the rules Claude gets name what the app also checks
        for word in ('どう動けばよいか', '安全', '予想', '出典は書かないでください', 'facts'):
            self.assertIn(word, compose.SYSTEM)


class PhoneComposeTest(unittest.TestCase):
    def setUp(self):
        compose._cache.clear()
        self.dir = tempfile.mkdtemp()
        p = mock.patch.object(phone, 'STATE', os.path.join(self.dir, '.phone', 'phone.json'))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir)

    def test_push_uses_checked_text_and_says_so(self):
        g = FakeGoogle({'lat': LAT, 'lon': LON, 'at': 0, 'token': 'fcm'})
        state = {'devices': {'mine': {'id': 'dev'}}, 'last': {}}
        with mock.patch.object(phone, 'here', return_value=out_for(warn(('49', 'landslide', 4)))), \
                mock.patch.object(phone, 'log'):
            text = phone.check_device(g, state, 'mine', writer=compose.FakeClient(GOOD))
        self.assertTrue(text.startswith('送りました'))
        body = g.sent[0][1]['body'].split('\n')
        self.assertEqual(body[0], '千代田区にレベル4土砂災害危険警報が出ていて、いる場所は土砂災害警戒区域（土石流）に入っています'
                                  '（気象庁・ハザードマップポータル）。')
        self.assertIn(phone.COMPOSED_NOTE, body)

    def test_push_falls_back_to_plain(self):
        g = FakeGoogle({'lat': LAT, 'lon': LON, 'at': time.time(), 'token': 'fcm'})
        state = {'devices': {'mine': {'id': 'dev'}}, 'last': {}}
        out = out_for(warn(('49', 'landslide', 4)))
        plain = out['notice']['body']
        with mock.patch.object(phone, 'here', return_value=out), mock.patch.object(phone, 'log'):
            phone.check_device(g, state, 'mine', writer=compose.FakeClient(with_line(3, '「C」があります。')))
        self.assertEqual(g.sent[0][1]['body'], plain)
        self.assertNotIn(phone.COMPOSED_NOTE, g.sent[0][1]['body'])


class ServerComposeTest(unittest.TestCase):
    """The page gets the plain text at once and asks /api/compose for Claude's text."""

    def setUp(self):
        compose._cache.clear()
        app.Handler.pending.clear()
        self.out = out_for(warn(('49', 'landslide', 4)))
        for p in (mock.patch.object(app, 'here', return_value=self.out),
                  mock.patch.object(app.Handler, 'writer', compose.FakeClient(GOOD)),
                  mock.patch.object(app.Handler, 'log_message')):
            p.start()
            self.addCleanup(p.stop)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_plain_first_then_claude(self):
        code, d = self.get(f'/api/here?lat={LAT}&lon={LON}')
        self.assertEqual(code, 200)
        n = d['notice']
        self.assertEqual(n['body'], self.out['notice']['body'])
        self.assertTrue(n['composer']['pending'])
        code, c = self.get('/api/compose?id=' + n['composer']['id'])
        self.assertEqual(code, 200)
        self.assertTrue(c['notice']['composer']['ok'])
        self.assertTrue(c['notice']['body'].endswith('（国土地理院）。'))
        # the next look-up already has Claude's text
        self.assertTrue(self.get(f'/api/here?lat={LAT}&lon={LON}')[1]['notice']['composer']['ok'])
        self.assertEqual(app.Handler.writer.calls, 1)

    def test_unknown_id(self):
        self.assertEqual(self.get('/api/compose?id=nope')[0], 404)


if __name__ == '__main__':
    unittest.main()
