"""Health checks e batimentos passivos; nunca inicia WSL."""
import os
from pathlib import Path
import queue
import threading
from . import agora, http_get


def _ler_batimento(caminho, ler_texto):
    arquivo = Path(caminho)
    mtime = arquivo.stat().st_mtime
    texto = arquivo.read_text(encoding='utf-8').strip() if ler_texto else 'batimento por mtime'
    return mtime, texto


def _batimento_limitado(caminho, ler_texto):
    # Um UNC indisponível pode bloquear o filesystem além do timeout HTTP.
    resultado = queue.Queue(maxsize=1)

    def ler():
        try:
            resultado.put(_ler_batimento(caminho, ler_texto))
        except Exception:
            resultado.put(None)

    threading.Thread(target=ler, daemon=True).start()
    try:
        return resultado.get(timeout=1)
    except queue.Empty:
        return None


def ler(ctx):
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
            batimento = _batimento_limitado(caminho, nome == 'Vigia')
            if batimento is None:
                raise OSError('batimento sem leitura')
            mtime, detalhe = batimento
            estado = 'ok' if 0 <= hora - mtime < ctx.get('batimento_max_s', 120) else 'fora'
        except OSError:
            pass
        servicos.append(dict(nome=nome, estado=estado, detalhe=detalhe, hora=hora))
    return {'servicos': servicos}
