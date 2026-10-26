"""Estado reconstruído a partir dos eventos locais do Claude."""
import json
from pathlib import Path
from . import agora, diretorio, epoch


def ler(ctx):
    sessoes = {}
    for nome in ('eventos.jsonl.1', 'eventos.jsonl'):
        try:
            stream = (diretorio(ctx) / nome).open(encoding='utf-8', errors='replace')
        except OSError:
            continue
        with stream:
            for linha in stream:
                try:
                    evento = json.loads(linha)
                except ValueError:
                    continue
                if not isinstance(evento, dict) or not evento.get('session_id'):
                    continue
                chave = evento['session_id']
                if evento.get('evento') == 'SessionEnd':
                    sessoes.pop(chave, None)
                else:
                    sessoes[chave] = evento
    resultado = []
    for chave, evento in sessoes.items():
        recente = False
        mtime = None
        hora_evento = epoch(evento.get('hora'))
        try:
            mtime = Path(evento['transcript_path']).stat().st_mtime
            recente = 0 <= agora(ctx) - mtime < 20
        except (OSError, KeyError, TypeError):
            pass
        if agora(ctx) - epoch(evento.get('hora')) >= 43200 and not recente:
            continue
        ultimo = evento.get('evento')
        estado = 'ocioso'
        if ultimo == 'PermissionRequest' and (mtime is None or mtime <= hora_evento + 2):
            estado = 'esperando permissão'
        elif recente and mtime > hora_evento + 3:
            estado = 'trabalhando'
        resultado.append(dict(evento, session_id=chave, estado=estado))
    return {'sessoes': resultado}
