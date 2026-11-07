"""Run the persona test: each simulated person reads the scenes and lists the 角 (corners) they hit.

python3 run.py --round r1 [--only P001,P002] [--first 5] [-j 3] [--model opus]
-> runs/<round>/<id>.json  (the person, their answer, cost and usage)

One `claude -p` call per person, with no tools, no MCP, no settings and no saved session. The Claude Code login
in use is the one CLAUDE_CONFIG_DIR points to (ANTHROPIC_API_KEY is dropped, so it never falls back to API
credit). People already done in this round are skipped, so a stopped run can be started again.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import personas  # noqa: E402


def ask(person, scenes, model, timeout):
    with open(os.path.join(HERE, 'schema.json'), encoding='utf-8') as f:
        schema = json.dumps(json.load(f), ensure_ascii=False)
    prompt = '\n'.join(['# あなたが演じる人', personas.describe(person), '', '# 場面', scenes])
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
    cmd = ['claude', '-p', '--model', model, '--tools', '', '--strict-mcp-config', '--setting-sources', '',
           '--no-session-persistence', '--disable-slash-commands', '--output-format', 'json',
           '--json-schema', schema, '--system-prompt-file', os.path.join(HERE, 'prompt.md')]
    with tempfile.TemporaryDirectory() as cwd:
        t0 = time.time()
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout)
        took = round(time.time() - t0, 1)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        return None, {'rc': p.returncode, 'stdout': p.stdout[-2000:], 'stderr': p.stderr[-2000:], 'took': took}
    if out.get('is_error') or not isinstance(out.get('structured_output'), dict):
        return None, {'rc': p.returncode, 'subtype': out.get('subtype'), 'result': str(out.get('result'))[-2000:],
                      'took': took}
    return {'answer': out['structured_output'], 'cost_usd': out.get('total_cost_usd'), 'usage': out.get('usage'),
            'models': list((out.get('modelUsage') or {}).keys()), 'took': took}, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--round', required=True, help='この回の名前（例 r1）。runs/<round>/ に書く')
    ap.add_argument('--only', help='この人だけ（例 P001,P007）')
    ap.add_argument('--first', type=int, help='はじめの N 人だけ')
    ap.add_argument('-j', type=int, default=3, help='同時に走らせる数')
    ap.add_argument('--model', default='opus')
    ap.add_argument('--timeout', type=int, default=900)
    a = ap.parse_args()
    with open(os.path.join(HERE, 'personas.json'), encoding='utf-8') as f:
        people = json.load(f)
    with open(os.path.join(HERE, 'scenes', 'all.txt'), encoding='utf-8') as f:
        scenes = f.read()
    scenes_sha = hashlib.sha256(scenes.encode()).hexdigest()
    if a.only:
        want = set(a.only.split(','))
        people = [p for p in people if p['id'] in want]
    if a.first:
        people = people[:a.first]
    out_dir = os.path.join(HERE, 'runs', a.round)
    os.makedirs(out_dir, exist_ok=True)
    todo = [p for p in people if not os.path.exists(os.path.join(out_dir, p['id'] + '.json'))]
    print(f'{a.round}: {len(todo)} 人（済み {len(people) - len(todo)}）, model {a.model}, -j {a.j}', file=sys.stderr)

    def one(p):
        try:
            res, err = ask(p, scenes, a.model, a.timeout)
        except subprocess.TimeoutExpired:
            res, err = None, {'timeout': a.timeout}
        if err:
            with open(os.path.join(out_dir, p['id'] + '.err.json'), 'w', encoding='utf-8') as f:
                json.dump(err, f, ensure_ascii=False, indent=1)
            return p['id'], None, err
        res.update({'person': p, 'model': a.model, 'scenes_sha256': scenes_sha,
                    'at': time.strftime('%Y-%m-%d %H:%M:%S')})
        with open(os.path.join(out_dir, p['id'] + '.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        return p['id'], res, None

    total = 0.0
    failed = 0
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
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
