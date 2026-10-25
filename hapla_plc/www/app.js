'use strict';
// Panel HAPLA PLC — ścieżki względne, bo HA podaje panel pod /api/hassio_ingress/<token>/.

const KW = ['PROGRAM', 'END_PROGRAM', 'FUNCTION_BLOCK', 'END_FUNCTION_BLOCK', 'FUNCTION', 'END_FUNCTION',
  'VAR', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT', 'VAR_GLOBAL', 'VAR_EXTERNAL', 'VAR_TEMP', 'END_VAR',
  'CONSTANT', 'RETAIN', 'AT', 'TYPE', 'END_TYPE', 'STRUCT', 'END_STRUCT', 'ARRAY', 'OF', 'IF', 'THEN',
  'ELSIF', 'ELSE', 'END_IF', 'CASE', 'END_CASE', 'FOR', 'TO', 'BY', 'DO', 'END_FOR', 'WHILE',
  'END_WHILE', 'REPEAT', 'UNTIL', 'END_REPEAT', 'EXIT', 'RETURN', 'CONTINUE', 'AND', 'OR', 'XOR',
  'NOT', 'MOD', 'TRUE', 'FALSE', 'INCLUDE', 'WAIT'];
const TYPES = ['BOOL', 'BYTE', 'WORD', 'DWORD', 'LWORD', 'SINT', 'INT', 'DINT', 'LINT', 'USINT', 'UINT',
  'UDINT', 'ULINT', 'REAL', 'LREAL', 'TIME', 'STRING'];
const words = (l) => new RegExp('\\b(?:' + l.join('|') + ')\\b', 'i');

CodeMirror.defineSimpleMode('iec-st', {
  start: [
    { regex: /\(\*/, token: 'comment', next: 'comment' },
    { regex: /\/\/.*/, token: 'comment' },
    { regex: /'(?:[^'$]|\$.)*'/, token: 'string' },
    { regex: /(?:T|TIME)#[\w.]+/i, token: 'number' },
    { regex: /\d+#[0-9A-Fa-f_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?/, token: 'number' },
    { regex: words(KW), token: 'keyword' },
    { regex: words(TYPES), token: 'type' },
    { regex: /%[IQM][XBWDL]?\d+(?:\.\d+)?/i, token: 'variable-3' },
    { regex: /:=|=>|<=|>=|<>|[-+*\/=<>]/, token: 'operator' },
    { regex: /[A-Za-z_]\w*(?=\s*\()/, token: 'def' },
    { regex: /[A-Za-z_]\w*/, token: 'variable' },
  ],
  comment: [
    { regex: /.*?\*\)/, token: 'comment', next: 'start' },
    { regex: /.*/, token: 'comment' },
  ],
  meta: { lineComment: '//', blockCommentStart: '(*', blockCommentEnd: '*)' },
});

const $ = (id) => document.getElementById(id);
const dark = matchMedia('(prefers-color-scheme: dark)');
const cm = CodeMirror($('editor'), {
  mode: 'iec-st', lineNumbers: true, indentUnit: 4, tabSize: 4, indentWithTabs: false,
  theme: dark.matches ? 'material-darker' : 'default',
  extraKeys: { 'Ctrl-S': () => save(), 'Cmd-S': () => save() },
});
dark.addEventListener('change', (e) => cm.setOption('theme', e.matches ? 'material-darker' : 'default'));

let current = null, clean = true, errors = [], status = {}, polling = false;

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts, headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(await r.text() || r.statusText);
  return r.json();
}

function toast(msg) { $('log').textContent = msg + '\n' + $('log').textContent; }

cm.on('change', () => {
  if (!current || current.readonly) return;
  clean = cm.isClean();
  $('filebar').textContent = current.name + (clean ? '' : ' — niezapisany');
});

async function save() {
  if (!current || current.readonly || clean) return;
  await api('api/file', { method: 'PUT', body: { name: current.name, text: cm.getValue() } });
  cm.markClean(); clean = true;
  $('filebar').textContent = current.name;
  await refresh();
}

async function open(name, line) {
  if (!current || current.name !== name) {
    await save();
    const f = await api('api/file?name=' + encodeURIComponent(name));
    current = f;
    cm.setValue(f.text); cm.clearHistory(); cm.markClean(); clean = true;
    cm.setOption('readOnly', f.readonly);
    $('filebar').textContent = name + (f.readonly ? ' — wygenerowany, tylko podgląd' : '');
    markErrors();
    renderFiles();
  }
  if (line) {
    cm.setCursor({ line: line - 1, ch: 0 });
    cm.scrollIntoView({ line: line - 1, ch: 0 }, 120);
    cm.focus();
  }
}

function markErrors() {
  for (let i = 0; i < cm.lineCount(); i++) cm.removeLineClass(i, 'background', 'cm-errline');
  if (!current) return;
  for (const e of errors) if (e.file === current.name.replace(/^build\//, '')) {
    cm.addLineClass(e.line - 1, 'background', 'cm-errline');
  }
}

let fileList = [];
function renderFiles() {
  const nav = $('files'); nav.innerHTML = '';
  const dirty = new Set((status.dirty || []).map((p) => p.split('/').pop()));
  const group = (title, names, ro) => {
    if (!names.length) return;
    const h = document.createElement('h2'); h.textContent = title; nav.append(h);
    for (const n of names) {
      const a = document.createElement('a');
      a.textContent = n.replace(/^build\//, '');
      a.title = n;
      a.className = (current && current.name === n ? 'active ' : '') + (ro ? 'ro' : '') +
        (dirty.has(n) ? ' dirty' : '');
      a.onclick = () => open(n).catch((e) => toast(e.message));
      nav.append(a);
    }
  };
  group('tools/plc/ctrl', fileList.filter((n) => !n.startsWith('build/')), false);
  group('wygenerowane (podgląd)', fileList.filter((n) => n.startsWith('build/')), true);
}

async function refresh() {
  status = await api('api/status');
  const s = $('status');
  if (!status.repo) {
    s.innerHTML = 'Brak klonu repozytorium — dodaj klucze wdrożeniowe (<b>Klucze</b>), potem <b>Pobierz z git</b>.';
  } else {
    const b = status.build;
    const build = !b ? '<span class="pill warn">nie kompilowano</span>'
      : b.current ? `<span class="pill ok">skompilowane ${b.time}</span>`
        : '<span class="pill warn">źródła zmienione od kompilacji</span>';
    s.innerHTML = `<b>${status.head || ''}</b> · ${status.dirty?.length ? status.dirty.length + ' zmienionych' : 'czysto'} · ${build}`;
    fileList = await api('api/files');
  }
  const busy = status.job;
  for (const id of ['bPull', 'bBuild', 'bCommit', 'bUpload']) $(id).disabled = busy;
  $('bBuild').disabled ||= !status.repo;
  $('bCommit').disabled ||= !status.repo || !status.dirty?.length;
  $('bUpload').disabled ||= !status.build?.current || !status.operator_pub;
  renderFiles();
  if (busy && !polling) poll();
}

function renderErrors() {
  const box = $('errors'); box.innerHTML = '';
  if (!errors.length) { box.innerHTML = '<div class="none">Brak błędów kompilacji</div>'; return; }
  for (const e of errors) {
    const d = document.createElement('div');
    d.innerHTML = `<span class="loc"></span> <span class="msg"></span>`;
    d.querySelector('.loc').textContent = `${e.file}:${e.line}`;
    d.querySelector('.msg').textContent = e.msg;
    d.onclick = () => open(e.file, e.line).catch((err) => toast(err.message));
    box.append(d);
  }
}

async function poll() {
  polling = true;
  try {
    for (;;) {
      const j = await api('api/job');
      const log = $('log'), atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
      log.textContent = `» ${j.name}\n` + j.log +
        (j.running ? '' : j.ok ? '\n✔ zakończone' : '\n✘ niepowodzenie');
      if (atEnd) log.scrollTop = log.scrollHeight;
      if (!j.running) {
        if (j.name === 'Kompilacja i testy') { errors = j.errors; renderErrors(); markErrors(); }
        break;
      }
      await new Promise((r) => setTimeout(r, 800));
    }
  } finally { polling = false; }
  if (current && !current.readonly && clean) {   // np. po git pull plik mógł się zmienić
    const f = await api('api/file?name=' + encodeURIComponent(current.name)).catch(() => null);
    if (f && f.text !== cm.getValue()) { const c = cm.getCursor(); cm.setValue(f.text); cm.markClean(); cm.setCursor(c); }
  }
  await refresh();
}

async function run(what, body) {
  try {
    await save();
    await api('api/' + what, { method: 'POST', body: body || {} });
    await refresh();
    poll();
  } catch (e) { toast(e.message); }
}

for (const d of document.querySelectorAll('dialog')) {
  d.querySelectorAll('[data-close]').forEach((b) => { b.onclick = () => d.close(); });
}
$('bPull').onclick = () => run('pull');
$('bSave').onclick = () => save().catch((e) => toast(e.message));
$('bBuild').onclick = () => run('build');
$('bCommit').onclick = () => { $('cMsg').value = ''; $('dCommit').showModal(); };
$('cGo').onclick = () => { $('dCommit').close(); run('commit', { message: $('cMsg').value }); };
$('bUpload').onclick = () => {
  const b = status.build;
  $('uInfo').textContent = `programs.ilb ${b.size} B, skompilowany ${b.time} z ${b.head}` +
    `${status.dirty?.length ? ' + niezacommitowane zmiany' : ''} → ${status.bridge}, klucz w slocie ${status.key_id}.`;
  $('uPass').value = ''; $('dUpload').showModal();
};
$('uGo').onclick = () => { const p = $('uPass').value; $('uPass').value = ''; $('dUpload').close(); run('upload', { password: p }); };
$('uPass').onkeydown = (e) => { if (e.key === 'Enter') $('uGo').click(); };

$('bKeys').onclick = () => {
  const k = $('kBody'); k.innerHTML = '';
  const add = (html) => { const d = document.createElement('div'); d.innerHTML = html; k.append(d); return d; };
  const pre = (text) => { const p = document.createElement('pre'); p.textContent = text; k.append(p); };
  add('<p>Klucze wdrożeniowe GitHub (zapis): dodaj każdy w <b>Settings → Deploy keys</b> właściwego repo, z zaznaczonym <b>Allow write access</b>.</p>');
  add('<b>HAPLA</b>'); pre(status.deploy_keys['github-hapla'] || '—');
  add('<b>STXC</b> (tylko odczyt wystarczy)'); pre(status.deploy_keys['github-stxc'] || '—');
  add('<b>Klucz operatora HA (ECDSA P-384)</b>');
  if (status.operator_pub) {
    pre(status.operator_pub);
    add(`<p>Wpisz go do whitelisty BRIDGE z PC kluczem, który już tam jest:</p>`);
    pre(`python3 scripts/bridge_tftp_push.py repo set --bridge ${status.bridge.split(':')[0]} \\\n` +
      `  --priv <twój_klucz.pem> --key-id <twój_slot> \\\n  --tag PKEY --target-id ${status.key_id} --payload-pem ha_operator.pub`);
  } else {
    const d = add('<p>Brak klucza. Hasło będzie potrzebne przy każdym wgraniu; HA go nie zapamiętuje.</p>' +
      '<input type="password" id="kPass" placeholder="Hasło (min. 8 znaków)" autocomplete="new-password">' +
      '<div class="row"><button class="primary" id="kGo">Utwórz klucz</button></div>');
    d.querySelector('#kGo').onclick = async () => {
      try {
        await api('api/key', { method: 'POST', body: { password: $('kPass').value } });
        await refresh(); $('dKeys').close(); $('bKeys').click();
      } catch (e) { alert(e.message); }
    };
  }
  $('dKeys').showModal();
};

window.addEventListener('beforeunload', (e) => { if (!clean) e.preventDefault(); });
refresh().then(() => { if (fileList.includes('main.st')) open('main.st'); }).catch((e) => toast(e.message));
