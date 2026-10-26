"""Interpretação limitada ao fim do transcript Claude."""
from . import linhas_finais, mascarar


def interpretar(linhas):
    resultado = {'modelo': None, 'pasta': None, 'fala': None, 'hora': None}
    for linha in linhas:
        resultado['hora'] = linha.get('timestamp', resultado['hora'])
        resultado['pasta'] = linha.get('cwd', resultado['pasta'])
        mensagem = linha.get('message') or {}
        if mensagem.get('model'):
            resultado['modelo'] = mensagem['model']
        if linha.get('type') == 'assistant':
            blocos = mensagem.get('content', [])
            texto = blocos if isinstance(blocos, str) else '\n'.join(
                b.get('text', '') for b in blocos if isinstance(b, dict) and b.get('type') == 'text')
            if texto:
                resultado['fala'] = mascarar(texto)[:200]
    return resultado


def ler(ctx):
    try:
        resultado = interpretar(linhas_finais(ctx['transcript_path']))
        if resultado['pasta'] is None:
            resultado['pasta'] = ctx.get('cwd')
        return resultado
    except (OSError, KeyError):
        return {'erro': 'transcript sem leitura'}
