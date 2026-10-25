#!/usr/bin/env python3
"""
HAPLA PLC — panel HA do programów ST dla BRIDGE.

Źródła żyją w klonie repo HAPLA (/data/repo, sparse: tools/plc, scripts, import/stxc).
Przebieg jak w CMake (app/bridge/CMakeLists.txt: bridge_ilb + plc_tests):
    make -C import/stxc release ilvm
    stage *.st + gen_hapla_regs.py → stxc -e ilb,map,json main.st
    tools/plc/ctrl/test/test_all.sh --ilb … --ilvm …
Wgrywanie: scripts/bridge_tftp_push.py z repo (FC 0x4A + TFTP) kluczem operatora HA.

Wszystkie operacje idą jako jedno zadanie w tle (job); panel odpytuje /api/job.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

DATA = Path(os.environ.get('HAPLA_PLC_DATA', '/data'))   # zmienna tylko do testów poza HA
REPO = DATA / 'repo'
STAGE = DATA / 'stage'
OUT = DATA / 'out'
SSH = DATA / '.ssh'
OPKEY = DATA / 'keys' / 'operator.pem'
CTRL = 'tools/plc/ctrl'
SPARSE = ['tools/plc', 'scripts', 'import/stxc']
WWW = Path(__file__).resolve().parent / 'www'
INGRESS_PROXY = '172.30.32.2'
NAME_RE = re.compile(r'^[A-Za-z0-9_.-]+\.st$')
ERR_RE = re.compile(r'([\w.-]+\.st):(\d+)[.:](\d+)\S*:?\s*(?:error:)?\s*(.*)')


# region ─── Zadanie w tle ───────────────────────────────────────────────────

class Job:
    def __init__(self):
        self.name, self.log, self.ok, self.running, self.errors = '', '', None, False, []

    def write(self, text: str) -> None:
        self.log += text

    def as_dict(self) -> dict:
        return {'name': self.name, 'log': self.log[-200_000:], 'ok': self.ok,
                'running': self.running, 'errors': self.errors}


JOB = Job()


async def sh(*cmd: str, cwd: Path | None = None, check: bool = True) -> int:
    JOB.write(f'$ {" ".join(cmd)}\n')
    p = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.STDOUT)
    async for line in p.stdout:
        JOB.write(re.sub(r'\x1b\[[0-9;]*m', '', line.decode('utf-8', 'replace')))
    rc = await p.wait()
    if check and rc:
        raise RuntimeError(f'polecenie zakończone kodem {rc}')
    return rc


def start(name: str, coro) -> web.Response:
    if JOB.running:
        raise web.HTTPConflict(text='Trwa inne zadanie')
    JOB.__init__()
    JOB.name, JOB.running = name, True

    async def run():
        try:
            await coro
            JOB.ok = True
        except Exception as e:  # noqa: BLE001 — wynik zadania idzie do panelu
            JOB.write(f'\nBŁĄD: {e}\n')
            JOB.ok = False
        finally:
            JOB.running = False
    asyncio.get_running_loop().create_task(run())
    return web.json_response({'started': name})

# endregion
# region ─── Git i klucze ────────────────────────────────────────────────────


def ssh_setup(opts: dict) -> None:
    """Dwa klucze wdrożeniowe (deploy key GitHuba działa tylko dla jednego repo) + aliasy hostów."""
    SSH.mkdir(mode=0o700, exist_ok=True)
    cfg = []
    git = lambda *a: subprocess.run(['git', 'config', '--global', *a], check=True)  # noqa: E731
    for alias, url in (('github-hapla', opts['repo_url']), ('github-stxc', opts['stxc_url'])):
        key = SSH / alias
        if not key.exists():
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', f'hapla-plc-ha-{alias}',
                            '-f', str(key)], check=True)
        cfg.append(f'Host {alias}\n  HostName github.com\n  User git\n  IdentityFile {key}\n'
                   f'  IdentitiesOnly yes\n  StrictHostKeyChecking yes\n')
        git(f'url.{alias}:{url.split(":", 1)[1]}.insteadOf', url)
    (SSH / 'config').write_text('\n'.join(cfg))
    git('user.name', opts['git_name'])
    git('user.email', opts['git_email'])


async def ensure_repo(opts: dict) -> None:
    if (REPO / '.git').exists():
        return
    await sh('git', 'clone', '--filter=blob:none', '--no-checkout', '--branch', opts['branch'],
             opts['repo_url'], str(REPO))
    await sh('git', 'sparse-checkout', 'set', *SPARSE, cwd=REPO)
    await sh('git', 'checkout', opts['branch'], cwd=REPO)
    await sh('git', 'submodule', 'update', '--init', 'import/stxc', cwd=REPO)


def src_files() -> list[Path]:
    d = REPO / CTRL
    return sorted(d.glob('*.st')) + [d / 'hapla-regs.json'] if d.exists() else []


def src_hash() -> str:
    h = hashlib.sha256()
    for f in src_files():
        h.update(f.name.encode() + b'\0' + f.read_bytes())
    return h.hexdigest()


async def git_out(*args: str) -> str:
    p = await asyncio.create_subprocess_exec('git', *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.DEVNULL)
    return (await p.communicate())[0].decode().rstrip()

# endregion
# region ─── Kompilacja, testy, commit, wgranie ──────────────────────────────


async def do_build() -> None:
    stxc = REPO / 'import/stxc'
    await sh('nice', 'make', '-j2', 'release', 'ilvm', cwd=stxc)
    shutil.rmtree(STAGE, ignore_errors=True)
    (STAGE / 'build').mkdir(parents=True)
    for f in (REPO / CTRL).glob('*.st'):
        shutil.copy2(f, STAGE)
    await sh('python3', str(REPO / CTRL / 'scripts/gen_hapla_regs.py'),
             '--json', str(REPO / CTRL / 'hapla-regs.json'), '--out-dir', str(STAGE / 'build'),
             '--only', 'defs.st,vars.st,defaults.mb')
    mark = len(JOB.log)
    rc = await sh(str(stxc / 'out/stxc'), '-e', 'ilb,map,json', 'main.st', cwd=STAGE, check=False)
    JOB.errors = [{'file': m[1], 'line': int(m[2]), 'col': int(m[3]), 'msg': m[4].strip()}
                  for m in ERR_RE.finditer(JOB.log[mark:])]
    if rc:
        raise RuntimeError('kompilacja nie powiodła się')
    await sh('bash', 'test/test_all.sh', '--ilb', str(STAGE / 'main.ilb'),
             '--ilvm', str(stxc / 'out/ilvm'), cwd=REPO / CTRL)
    OUT.mkdir(exist_ok=True)
    shutil.copy2(STAGE / 'main.ilb', OUT / 'programs.ilb')
    meta = {'src_hash': src_hash(), 'head': await git_out('rev-parse', '--short', 'HEAD'),
            'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'size': (OUT / 'programs.ilb').stat().st_size}
    (OUT / 'meta.json').write_text(json.dumps(meta))
    JOB.write(f'\nprograms.ilb: {meta["size"]} B, testy OK\n')


async def do_pull(opts: dict) -> None:
    await ensure_repo(opts)
    await sh('git', 'pull', '--rebase', '--autostash', 'origin', opts['branch'], cwd=REPO)
    await sh('git', 'submodule', 'update', '--init', 'import/stxc', cwd=REPO)


async def do_commit(opts: dict, message: str) -> None:
    await sh('git', 'add', '-A', CTRL, cwd=REPO)
    await sh('git', 'commit', '-m', message, cwd=REPO)
    if await sh('git', 'push', 'origin', f'HEAD:{opts["branch"]}', cwd=REPO, check=False):
        JOB.write('push odrzucony — dociągam zmiany i ponawiam\n')
        await sh('git', 'pull', '--rebase', 'origin', opts['branch'], cwd=REPO)
        await sh('git', 'push', 'origin', f'HEAD:{opts["branch"]}', cwd=REPO)


def push_ilb(opts: dict, password: str) -> None:
    """bridge_tftp_push.cmd_push z repo; klucz odszyfrowany tu, hasło nie trafia na dysk."""
    priv = serialization.load_pem_private_key(OPKEY.read_bytes(), password=password.encode())
    spec = importlib.util.spec_from_file_location('btp', REPO / 'scripts/bridge_tftp_push.py')
    btp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(btp)
    btp.load_privkey = lambda _path: priv
    args = argparse.Namespace(bridge=opts['bridge_host'], port=opts['bridge_port'],
                              unit=opts['bridge_unit'], key_id=opts['key_id'],
                              tftp_port=btp.DEFAULT_TFTP_PORT, priv=str(OPKEY),
                              local=str(OUT / 'programs.ilb'), remote='programs.ilb')
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        rc = btp.cmd_push(args)
    JOB.write(buf.getvalue())
    if rc:
        raise RuntimeError('BRIDGE odrzucił wgranie')


async def do_upload(opts: dict, password: str) -> None:
    meta = json.loads((OUT / 'meta.json').read_text())
    if meta['src_hash'] != src_hash():
        raise RuntimeError('źródła zmieniły się od ostatniej kompilacji — skompiluj ponownie')
    JOB.write(f'Wgrywam programs.ilb ({meta["size"]} B, {meta["time"]}, HEAD {meta["head"]})\n')
    await asyncio.to_thread(push_ilb, opts, password)

# endregion
# region ─── HTTP ────────────────────────────────────────────────────────────


def safe_path(name: str) -> Path:
    if not (NAME_RE.match(name) or name == 'hapla-regs.json'):
        raise web.HTTPBadRequest(text='Niedozwolona nazwa pliku')
    return REPO / CTRL / name


def make_app(opts: dict, allow: set[str]) -> web.Application:
    @web.middleware
    async def only_ingress(request, handler):
        if request.remote not in allow:
            raise web.HTTPForbidden()
        return await handler(request)

    async def index(_):
        return web.FileResponse(WWW / 'index.html')

    async def status(_):
        st = {'repo': (REPO / '.git').exists(), 'job': JOB.running, 'deploy_keys': {},
              'operator_pub': None, 'bridge': f"{opts['bridge_host']}:{opts['bridge_port']}",
              'key_id': opts['key_id'], 'build': None}
        for alias in ('github-hapla', 'github-stxc'):
            p = SSH / f'{alias}.pub'
            st['deploy_keys'][alias] = p.read_text().strip() if p.exists() else None
        if OPKEY.with_suffix('.pub').exists():
            st['operator_pub'] = OPKEY.with_suffix('.pub').read_text()
        if st['repo'] and not JOB.running:
            st['head'] = await git_out('log', '-1', '--format=%h %s (%cr)')
            st['dirty'] = [l[3:] for l in (await git_out('status', '--porcelain', CTRL)).splitlines()]
            if (OUT / 'meta.json').exists():
                meta = json.loads((OUT / 'meta.json').read_text())
                st['build'] = {**meta, 'current': meta['src_hash'] == src_hash()}
        return web.json_response(st)

    async def files(_):
        return web.json_response([f.name for f in src_files()] +
                                 [f'build/{f.name}' for f in sorted((STAGE / 'build').glob('*.st'))])

    async def get_file(request):
        name = request.query['name']
        if name.startswith('build/'):   # wygenerowane z hapla-regs.json — tylko podgląd
            p = STAGE / 'build' / Path(name).name
        else:
            p = safe_path(name)
        if not p.exists():
            raise web.HTTPNotFound()
        return web.json_response({'name': name, 'text': p.read_text(encoding='utf-8'),
                                  'readonly': name.startswith('build/')})

    async def put_file(request):
        body = await request.json()
        if JOB.running:
            raise web.HTTPConflict(text='Trwa zadanie — zapis wstrzymany')
        safe_path(body['name']).write_text(body['text'], encoding='utf-8')
        return web.json_response({'saved': body['name']})

    async def job(_):
        return web.json_response(JOB.as_dict())

    async def act(request):
        body = await request.json() if request.can_read_body else {}
        what = request.match_info['what']
        if what == 'pull':
            return start('Pobierz z git', do_pull(opts))
        if not (REPO / '.git').exists():
            raise web.HTTPConflict(text='Najpierw pobierz repozytorium')
        if what == 'build':
            return start('Kompilacja i testy', do_build())
        if what == 'commit':
            msg = body.get('message', '').strip()
            if not msg:
                raise web.HTTPBadRequest(text='Pusty opis zmian')
            return start('Commit i push', do_commit(opts, msg))
        if what == 'upload':
            if not OPKEY.exists():
                raise web.HTTPConflict(text='Brak klucza operatora')
            return start('Wgrywanie do BRIDGE', do_upload(opts, body.get('password', '')))
        raise web.HTTPNotFound()

    async def make_key(request):
        body = await request.json()
        if OPKEY.exists():
            raise web.HTTPConflict(text='Klucz już istnieje')
        if len(body.get('password', '')) < 8:
            raise web.HTTPBadRequest(text='Hasło musi mieć co najmniej 8 znaków')
        priv = ec.generate_private_key(ec.SECP384R1())
        OPKEY.parent.mkdir(mode=0o700, exist_ok=True)
        OPKEY.write_bytes(priv.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(body['password'].encode())))
        OPKEY.with_suffix('.pub').write_bytes(priv.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        return web.json_response({'created': True})

    app = web.Application(middlewares=[only_ingress], client_max_size=4 * 1024 * 1024)
    app.router.add_get('/', index)
    app.router.add_static('/www', WWW)
    app.router.add_get('/api/status', status)
    app.router.add_get('/api/files', files)
    app.router.add_get('/api/file', get_file)
    app.router.add_put('/api/file', put_file)
    app.router.add_get('/api/job', job)
    app.router.add_post('/api/key', make_key)
    app.router.add_post('/api/{what}', act)
    return app

# endregion


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--options', type=Path, required=True)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--allow', action='append', default=[INGRESS_PROXY],
                    help='dodatkowy dozwolony adres klienta (testy poza HA)')
    a = ap.parse_args()
    opts = json.loads(a.options.read_text())
    ssh_setup(opts)
    web.run_app(make_app(opts, set(a.allow)), port=a.port, print=None)


if __name__ == '__main__':
    main()
