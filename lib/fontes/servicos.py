"""Health checks e batimentos passivos; nunca inicia WSL."""
import os
import json
import math
import time
from datetime import datetime
from pathlib import Path
import queue
import subprocess
import threading
from . import agora, http_get
from lib.semjanela import sem_janela_kwargs

_WSL_CACHE = {'ate': 0.0, 'rodando': None}
_WSL_TTL_S = 45


def _ler_batimento(caminho, ler_texto):
    arquivo = Path(caminho)
    mtime = arquivo.stat().st_mtime
    texto = arquivo.read_text(encoding='utf-8').strip() if ler_texto else 'batimento por mtime'
    if ler_texto and texto.startswith('{'):
        dado = json.loads(texto)
        ts, pid = dado.get('ts'), dado.get('pid')
        if (not isinstance(ts, (int, float)) or isinstance(ts, bool) or not math.isfinite(ts)
                or not isinstance(pid, int) or isinstance(pid, bool)):
            raise ValueError('batimento JSON inválido: ts/pid')
        horario = datetime.fromisoformat(dado['hora']).strftime('%H:%M:%S')
        return ts, 'pid %d · %s' % (pid, horario)
    if ler_texto:
        datetime.strptime(texto, '%H:%M:%S')
    return mtime, texto


def _batimento_limitado(caminho, ler_texto):
    # Um UNC indisponível pode bloquear o filesystem além do timeout HTTP.
    resultado = queue.Queue(maxsize=1)

    def ler():
        try:
            resultado.put(_ler_batimento(caminho, ler_texto))
        except Exception as erro:
            resultado.put(erro)

    threading.Thread(target=ler, daemon=True).start()
    try:
        valor = resultado.get(timeout=1)
        if isinstance(valor, Exception):
            raise valor
        return valor
    except queue.Empty:
        return None


def _distro_do_unc(caminho):
    partes = [p for p in str(caminho).replace(chr(92), '/').split('/') if p]
    if len(partes) >= 2 and partes[0].lower() in ('wsl.localhost', 'wsl$'):
        return partes[1]
    return None


def _distros_rodando(usar_cache):
    # Listar distros não as acorda; acessar o UNC de uma parada, sim.
    agora_m = time.monotonic()
    if usar_cache and agora_m < _WSL_CACHE['ate']:
        return _WSL_CACHE['rodando']
    try:
        r = subprocess.run(['wsl.exe', '--list', '--running', '--quiet'], capture_output=True,
                           timeout=2, **sem_janela_kwargs())
        if r.returncode != 0:
            raise OSError('wsl.exe retornou %s' % r.returncode)
        texto = r.stdout.decode('utf-16-le', errors='replace').replace(chr(0xfeff), '')
        rodando = {l.strip().lower() for l in texto.splitlines() if l.strip()}
    except Exception:
        rodando = None  # falha fechada
    if usar_cache:
        _WSL_CACHE.update(ate=agora_m + _WSL_TTL_S, rodando=rodando)
    return rodando


def _distro_acordada(caminho, usar_cache):
    distro = _distro_do_unc(caminho)
    if distro is None:
        return True
    rodando = _distros_rodando(usar_cache)
    return rodando is not None and distro.lower() in rodando


def ler(ctx):
    inicio = time.monotonic()
    hora = agora(ctx)
    servicos = []
    for nome, url in (('Hindsight', 'http://127.0.0.1:8888/health'),
                      ('Ollama', 'http://127.0.0.1:11434/api/ps')):
        try:
            dado = http_get(ctx, url, 1)
            ok = dado.get('status') == 'healthy' if nome == 'Hindsight' else isinstance(dado.get('models'), list)
            estado, detalhe = ('ok', 'resposta válida') if ok else ('fora', 'resposta inesperada')
        except Exception:
            estado, detalhe = 'fora', 'sem resposta'
        servicos.append(dict(nome=nome, estado=estado, detalhe=detalhe, hora=hora))
    vigia = ctx.get('vigia_path', r'\\wsl.localhost\Ubuntu-26.04\home\administrador\ferramentas\painel-vigia\vigia.batimento')
    hermes = ctx.get('hermes_path') or Path(os.environ.get('LOCALAPPDATA', '')) / 'hermes' / 'cron' / 'ticker_heartbeat'
    for nome, caminho in (('Vigia', vigia), ('Hermes', hermes)):
        estado, detalhe = 'sem leitura', 'batimento ausente'
        try:
            if os.name != 'nt' and nome == 'Vigia' and 'vigia_path' not in ctx:
                raise FileNotFoundError
            if nome == 'Vigia' and not _distro_acordada(caminho, 'vigia_path' not in ctx):
                raise OSError('distro WSL parada ou indisponível; leitura omitida')
            batimento = _batimento_limitado(caminho, nome == 'Vigia')
            if batimento is None:
                raise OSError('batimento sem leitura')
            mtime, detalhe = batimento
            # ctx.agora é o snapshot da passada; os health checks já consumiram tempo.
            observado = hora + (time.monotonic() - inicio)
            estado = 'ok' if abs(observado - mtime) < ctx.get('batimento_max_s', 120) else 'fora'
        except (OSError, ValueError, TypeError, KeyError) as erro:
            detalhe = '%s: %s' % (type(erro).__name__, erro)
        servicos.append(dict(nome=nome, estado=estado, detalhe=detalhe, hora=hora))
    return {'servicos': servicos}
