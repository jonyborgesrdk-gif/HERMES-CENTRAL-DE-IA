"""Estado reconstruído a partir dos eventos locais do Claude."""
import json
from pathlib import Path
from . import agora, diretorio, epoch


def ler(ctx):
    sessoes = {}
    try:
        with (diretorio(ctx) / 'eventos.jsonl').open(encoding='utf-8') as stream:
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
    except OSError:
        return {'sessoes': []}
    resultado = []
    for chave, evento in sessoes.items():
        recente = False
        try:
            recente = 0 <= agora(ctx) - Path(evento['transcript_path']).stat().st_mtime < 20
        except (OSError, KeyError, TypeError):
            pass
        if agora(ctx) - epoch(evento.get('hora')) >= 43200 and not recente:
            continue
        ultimo = evento.get('evento')
        estado = 'ocioso'
        if ultimo == 'PermissionRequest':
            estado = 'esperando permissão'
        elif ultimo != 'Stop' and recente:
            estado = 'trabalhando'
        resultado.append(dict(evento, session_id=chave, estado=estado))
    return {'sessoes': resultado}
