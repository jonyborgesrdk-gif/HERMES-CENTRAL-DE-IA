"""Fontes independentes: ler(ctx) -> dict, sem integração com o coletor.

ctx: dir (dados do painel), agora (epoch ou datetime), executar(argv, timeout),
http_get(url, timeout, headers), listar_arquivos(raiz). Injeções são opcionais;
os adaptadores abaixo são as únicas fronteiras de rede e subprocesso.
"""
import json
import os
from pathlib import Path
import ssl
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def diretorio(ctx):
    return Path(ctx.get('dir') or os.environ.get('PAINEL_IAS_DIR') or
                Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'painel-ias')


def agora(ctx):
    valor = ctx.get('agora', time.time())
    return valor.timestamp() if hasattr(valor, 'timestamp') else float(valor)


def epoch(valor):
    from datetime import datetime
    try:
        return float(valor)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(valor).replace('Z', '+00:00')).timestamp()
        except ValueError:
            return 0


def executar(ctx, argv, timeout):
    if 'executar' in ctx:
        return ctx['executar'](argv, timeout=timeout)
    return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout, check=True).stdout


def http_get(ctx, url, timeout, headers=None):
    if 'http_get' in ctx:
        return ctx['http_get'](url, timeout=timeout, headers=headers or {})
    parsed = urlsplit(url)
    if parsed.hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('somente endpoints locais')
    # Não seguir redirects: preserva a restrição de host e o token local.
    from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler

    class SemRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    context = ssl._create_unverified_context() if parsed.scheme == 'https' else None
    opener = build_opener(SemRedirect(), HTTPSHandler(context=context))
    with opener.open(Request(url, headers=headers or {}), timeout=timeout) as response:
        return json.load(response)


def linhas_finais(caminho, limite=65536):
    with Path(caminho).open('rb') as stream:
        stream.seek(0, 2)
        inicio = max(0, stream.tell() - limite)
        stream.seek(inicio)
        if inicio:
            stream.readline()  # descarta fragmento da primeira linha
        linhas = stream.read().decode('utf-8', errors='replace').splitlines()
    for linha in linhas:
        try:
            valor = json.loads(linha)
        except ValueError:
            continue
        if isinstance(valor, dict):
            yield valor


def mascarar(texto):
    try:
        from ..leitor import mascarar as aplicar
    except ImportError:
        from leitor import mascarar as aplicar
    return aplicar(str(texto))
