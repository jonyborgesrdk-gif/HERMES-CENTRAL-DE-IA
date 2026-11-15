"""Interpretação limitada ao fim do transcript Claude."""
from . import linhas_finais


def interpretar(linhas):
    resultado = {'modelo': None, 'pasta': None, 'hora': None}
    for linha in linhas:
        resultado['hora'] = linha.get('timestamp', resultado['hora'])
        resultado['pasta'] = linha.get('cwd', resultado['pasta'])
        mensagem = linha.get('message') or {}
        if mensagem.get('model'):
            resultado['modelo'] = mensagem['model']
    return resultado


def ler(ctx):
    if not ctx.get('transcript_path'):
        return None
    try:
        resultado = interpretar(linhas_finais(ctx['transcript_path']))
        if resultado['pasta'] is None:
            resultado['pasta'] = ctx.get('cwd')
        return resultado
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return {'erro': 'transcript sem leitura'}
