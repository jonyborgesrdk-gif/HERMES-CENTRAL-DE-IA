"""Contagem passiva dos executáveis conhecidos."""
import csv
import io
import os
from . import executar

NOMES = ('claude.exe', 'codex.exe', 'grok.exe', 'hermes.exe', 'maestri.exe',
         'copilot.exe', 'node.exe')


def interpretar(texto):
    r = {}
    for linha in csv.reader(io.StringIO(texto)):
        nome = linha[0].lower() if linha else ''
        if nome in NOMES:
            r[nome] = r.get(nome, 0) + 1
    return r


def ler(ctx):
    if os.name != 'nt' and 'executar' not in ctx:
        return {}
    try:
        return interpretar(executar(ctx, ['tasklist', '/FO', 'CSV', '/NH'], 3))
    except Exception:
        return {}
