"""Cota Claude observada via /usage, com cache de cinco minutos."""
import json
import os
import re
import tempfile
from . import agora, diretorio, executar


def ler_usage(texto):
    r = dict(sessao_5h=None, semanal=None, reset_5h=None, reset_semanal=None)
    for linha in texto.splitlines():
        # Não confundir cota semanal por modelo com a semanal geral.
        chave = None
        if re.search(r'Current session\s*:', linha, re.I):
            chave, reset = 'sessao_5h', 'reset_5h'
        elif re.search(r'(?:Current week|Weekly)(?:\s*\(all models\))?\s*:', linha, re.I):
            chave, reset = 'semanal', 'reset_semanal'
        if chave:
            m = re.search(r'(\d{1,3})%\s*used', linha, re.I)
            if m and 0 <= int(m[1]) <= 100:
                r[chave] = int(m[1])
            m = re.search(r'\bresets\s+(.+)', linha, re.I)
            if m:
                r[reset] = m[1].strip()
    return r


def ler(ctx):
    try:
        r = json.loads((diretorio(ctx) / 'cota-claude.json').read_text(encoding='utf-8'))
        return r if isinstance(r, dict) else {}
    except (OSError, ValueError):
        return {}


def atualizar(ctx):
    anterior = ler(ctx)
    if 0 <= agora(ctx) - anterior.get('tentativa', anterior.get('hora', 0)) < 300:
        return anterior
    r = dict(anterior, tentativa=agora(ctx))
    try:
        saida = executar(ctx, ['claude', '--safe-mode', '--no-session-persistence',
                              '-p', '/usage', '--output-format', 'stream-json', '--verbose'], 60)
        textos = []
        for linha in saida.splitlines():
            d = json.loads(linha)
            if d.get('type') == 'assistant':
                textos.extend(b['text'] for b in d.get('message', {}).get('content', [])
                              if b.get('type') == 'text')
        cotas = ler_usage('\n'.join(textos))
        if cotas['sessao_5h'] is None and cotas['semanal'] is None:
            raise ValueError('sem cota observada')
        r = dict(cotas, hora=agora(ctx), tentativa=agora(ctx))
    except Exception:
        r['erro'] = 'consulta de cota falhou'
    pasta = diretorio(ctx)
    tmp = None
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=pasta, delete=False) as f:
            tmp = f.name
            json.dump(r, f)
        os.replace(tmp, pasta / 'cota-claude.json')
    except OSError:
        r['erro'] = 'cache sem escrita'
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return r
