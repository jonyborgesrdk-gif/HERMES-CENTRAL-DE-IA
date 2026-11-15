"""Cota do Codex observada nos arquivos locais de sessão (opt-in, sem rede).

Lê os arquivos ``*.jsonl`` e ``*.jsonl.zst`` de ``~/.codex/sessions`` (só os
MAX_ARQUIVOS de modificação mais recente) e publica o evento de limites de
MAIOR ``timestamp``. O horário publicado é o do evento, nunca o do arquivo.
Só saem números e textos de lista fixa: nenhum caminho, nome de arquivo, id de
sessão, ``limit_id``, ``plan_type`` ou crédito sai daqui.
"""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from compression import zstd
except ImportError:
    zstd = None

from . import agora

CHAVE = "cota_codex_local"
MAX_ARQUIVOS = 8
MAX_BYTES_POR_ARQUIVO = 64 * 1024 * 1024
TOLERANCIA_FUTURO_S = 300
ERRO_LISTAR = "cota Codex local: pasta de sessões sem leitura"
VAZIO = {"estado": "sem_dado", "observado_epoch": None, "barras": []}

_CHAVES_JANELA = ("primary", "secondary")
_TIPOS = {300: "5h", 10080: "semana"}
_MAX_JANELA_MIN = 525600


def _vazio():
    # Cópia com lista própria: quem recebe a saída não altera a constante.
    return dict(VAZIO, barras=[])


def _numerico(valor):
    """D14: int ou float, nunca bool, finito; inteiro grande demais não é numérico."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return False
    try:
        return math.isfinite(valor)
    except OverflowError:
        return False


def instante(valor):
    """Epoch em segundos de um ISO 8601 (sem fuso = UTC) ou de um número > 0."""
    if isinstance(valor, str):
        try:
            momento = datetime.fromisoformat(valor.replace('Z', '+00:00'))
            if momento.tzinfo is None:
                momento = momento.replace(tzinfo=timezone.utc)
            resultado = momento.timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            return None
    elif _numerico(valor) and valor > 0:
        resultado = float(valor)
    else:
        return None
    return resultado if math.isfinite(resultado) else None


def janelas(rate_limits):
    """Barras de `primary` e `secondary`, nesta ordem; janela inválida é pulada."""
    barras = []
    if not isinstance(rate_limits, dict):
        return barras
    for chave in _CHAVES_JANELA:
        janela = rate_limits.get(chave)
        if not isinstance(janela, dict):
            continue
        usado = janela.get("used_percent")
        if not (_numerico(usado) and 0 <= usado <= 100):
            continue
        minutos = janela.get("window_minutes")
        if (isinstance(minutos, bool) or not isinstance(minutos, int)
                or not 1 <= minutos <= _MAX_JANELA_MIN):
            continue
        reset = janela.get("resets_at")
        barras.append({
            "tipo": _TIPOS.get(minutos, "janela"),
            "usado_pct": float(usado),
            "renova_epoch": reset if _numerico(reset) and reset > 0 else None,
            "janela_min": minutos,
        })
    return barras


def ultimo_evento(linhas, agora_s):
    """(epoch, barras) do evento de limites de maior `timestamp`, ou None."""
    melhor = None
    limite = agora_s + TOLERANCIA_FUTURO_S
    for linha in linhas:
        if '"rate_limits"' not in linha:
            continue
        try:
            evento = json.loads(linha)
        except (ValueError, RecursionError):
            continue
        if not isinstance(evento, dict) or evento.get("type") != "event_msg":
            continue
        payload = evento.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        rate_limits = payload.get("rate_limits")
        if not isinstance(rate_limits, dict):
            continue
        epoch = instante(evento.get("timestamp"))
        if epoch is None or epoch > limite:
            continue
        barras = janelas(rate_limits)
        if not barras:
            continue
        if melhor is None or epoch >= melhor[0]:
            melhor = (epoch, barras)
    return melhor


def _linhas(caminho):
    """Linhas de texto do arquivo; para quando a soma passa de MAX_BYTES_POR_ARQUIVO."""
    if str(caminho).lower().endswith('.zst'):
        stream = zstd.open(caminho, 'rt', encoding='utf-8', errors='replace')
    else:
        stream = open(caminho, 'r', encoding='utf-8', errors='replace')
    total = 0
    with stream:
        while True:
            # Lê no máximo o que falta para passar do limite: uma linha enorme
            # não é carregada inteira na memória.
            linha = stream.readline(MAX_BYTES_POR_ARQUIVO - total + 1)
            if not linha:
                return
            total += len(linha)
            if total > MAX_BYTES_POR_ARQUIVO:
                return
            yield linha


def ler(ctx):
    raiz = Path(ctx.get('sessoes_dir') or Path.home() / '.codex' / 'sessions')
    agora_s = agora(ctx)
    if not raiz.is_dir():
        return {CHAVE: _vazio()}
    padroes = ['*.jsonl'] + (['*.jsonl.zst'] if zstd is not None else [])
    try:
        encontrados = [caminho for padrao in padroes for caminho in raiz.rglob(padrao)]
    except OSError:
        return {"erro": ERRO_LISTAR}
    datados = []
    for caminho in encontrados:
        try:
            datados.append((os.stat(caminho).st_mtime, caminho))
        except OSError:
            continue  # sumiu entre a listagem e a consulta
    datados.sort(key=lambda item: item[0], reverse=True)
    melhor = None
    for _, caminho in datados[:MAX_ARQUIVOS]:
        try:
            achado = ultimo_evento(_linhas(caminho), agora_s)
        except Exception:
            continue  # arquivo ilegível ou corrompido: só ele é ignorado
        if achado is not None and (melhor is None or achado[0] > melhor[0]):
            melhor = achado
    if melhor is None:
        return {CHAVE: _vazio()}
    return {CHAVE: {"estado": "ok", "observado_epoch": melhor[0], "barras": melhor[1]}}
