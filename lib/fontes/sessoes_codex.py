"""Descoberta de sessões Codex; porcentagens representam USADO."""
from pathlib import Path
import json
from . import agora


def interpretar(linhas):
    r = {'session_id': None, 'pasta': None, 'modelo': None,
         'sessao_5h': None, 'semanal': None, 'reset_5h': None,
         'reset_semanal': None, 'hora': None}
    for linha in linhas:
        if not isinstance(linha, dict):
            continue
        p = linha.get('payload') or {}
        if not isinstance(p, dict):
            continue
        r['hora'] = linha.get('timestamp', r['hora'])
        if linha.get('type') in ('session_meta', 'turn_context'):
            r['pasta'] = p.get('cwd', r['pasta'])
            r['modelo'] = p.get('model', r['modelo'])
            if linha['type'] == 'session_meta':
                r['session_id'] = p.get('session_id') or p.get('id')
        if linha.get('type') == 'event_msg' and p.get('type') == 'token_count':
            limits = p.get('rate_limits') or {}
            for chave, reset, minutos in (('sessao_5h', 'reset_5h', 300),
                                           ('semanal', 'reset_semanal', 10080)):
                janela = next((v for v in limits.values() if isinstance(v, dict)
                               and v.get('window_minutes') == minutos), {})
                r[chave] = janela.get('used_percent')
                r[reset] = janela.get('resets_at')
    return r


def ler(ctx):
    raiz = Path(ctx.get('sessoes_dir') or Path.home() / '.codex' / 'sessions')
    arquivos = ctx.get('listar_arquivos', lambda p: p.rglob('*.jsonl'))(raiz)
    sessoes = []
    for arquivo in arquivos:
        try:
            idade = agora(ctx) - Path(arquivo).stat().st_mtime
            if idade >= 86400 or idade < 0:
                continue
            # Leitura em streaming preserva metadados do início sem carregar o arquivo inteiro.
            def linhas():
                with Path(arquivo).open(encoding='utf-8') as stream:
                    for linha in stream:
                        try:
                            yield json.loads(linha)
                        except ValueError:
                            continue
            r = interpretar(linhas())
            r['estado'] = 'trabalhando' if idade < 20 else 'ocioso'
            sessoes.append(r)
        except (OSError, ValueError):
            continue
    return {'sessoes': sessoes}
