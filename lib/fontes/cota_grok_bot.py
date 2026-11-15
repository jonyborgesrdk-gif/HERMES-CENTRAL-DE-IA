"""Uso do Grok Bot observado nos arquivos locais do aplicativo (opt-in, sem rede).

Lê só os arquivos ``*.blob`` (JSON) de ``%APPDATA%\\Grok Bot\\sand-client-persistence``
e publica o registro com ``usage.percentUsed`` de MAIOR ``readAtMs``. O horário
publicado é o da leitura registrada pelo aplicativo, nunca o do arquivo.
Só saem números e textos de lista fixa: nome de arquivo, chave opaca, rótulos
de plano, time e saldo nunca saem daqui.
"""
import json
import math
import os

from . import agora

CHAVE = "cota_grok_bot"
MAX_ARQUIVOS = 400
MAX_BYTES = 2_000_000
TOLERANCIA_FUTURO_S = 300
ERRO_LISTAR = "cota Grok Bot: pasta sem leitura"
VAZIO = {"estado": "sem_dado", "observado_epoch": None, "barras": []}

# Prefixo de caminho longo do Windows: barra invertida, barra invertida,
# interrogação, barra invertida.
_PREFIXO_LONGO = '\\\\?\\'
_BARRAS_DUPLAS = '\\\\'
_EXTENSAO = '.blob'


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


def pasta_padrao(ambiente=None):
    ambiente = os.environ if ambiente is None else ambiente
    appdata = ambiente.get('APPDATA')
    if not appdata:
        return None
    return os.path.join(appdata, 'Grok Bot', 'sand-client-persistence')


def caminho_longo(caminho):
    """No Windows, caminho absoluto com o prefixo de caminho longo (nomes passam de 260)."""
    if os.name != 'nt':
        return str(caminho)
    absoluto = os.path.abspath(str(caminho))
    if absoluto.startswith(_BARRAS_DUPLAS):
        return absoluto
    return _PREFIXO_LONGO + absoluto


def candidatos(objeto):
    """Lista de (readAtMs, usage) do próprio objeto e de cada valor de objeto["value"]."""
    if not isinstance(objeto, dict):
        return []
    nos = [objeto]
    valor = objeto.get("value")
    if isinstance(valor, dict):
        nos.extend(valor.values())
    achados = []
    for no in nos:
        if not isinstance(no, dict):
            continue
        usage = no.get("usage")
        lido = no.get("readAtMs")
        if isinstance(usage, dict) and _numerico(lido) and lido > 0:
            achados.append((lido, usage))
    return achados


def barra(usage):
    """Barra "janela" com o percentual usado; None se `percentUsed` não vale."""
    if not isinstance(usage, dict):
        return None
    usado = usage.get("percentUsed")
    if not (_numerico(usado) and 0 <= usado <= 100):
        return None
    reset = usage.get("nextResetMs")
    return {
        "tipo": "janela",
        "usado_pct": float(usado),
        "renova_epoch": reset / 1000 if _numerico(reset) and reset > 0 else None,
        "janela_min": None,
    }


def _arquivos(pasta):
    """Caminhos dos `.blob` de até MAX_BYTES, do mais novo para o mais velho."""
    datados = []
    with os.scandir(pasta) as entradas:
        for entrada in entradas:
            if not entrada.name.lower().endswith(_EXTENSAO):
                continue
            try:
                if not entrada.is_file(follow_symlinks=False):
                    continue
                info = entrada.stat(follow_symlinks=False)
            except OSError:
                continue  # sumiu ou ficou ilegível entre a listagem e a consulta
            if info.st_size > MAX_BYTES:
                continue
            datados.append((info.st_mtime, entrada.path))
    datados.sort(key=lambda item: item[0], reverse=True)
    return [caminho for _, caminho in datados[:MAX_ARQUIVOS]]


def _objeto(caminho):
    with open(caminho, 'rb') as arquivo:
        dados = arquivo.read(MAX_BYTES + 1)
    if len(dados) > MAX_BYTES:
        return None  # cresceu depois da consulta do tamanho
    return json.loads(dados.decode('utf-8', errors='replace'))


def ler(ctx):
    pasta = ctx.get('grok_bot_dir') or pasta_padrao()
    agora_s = agora(ctx)
    if pasta is None or not os.path.isdir(caminho_longo(pasta)):
        return {CHAVE: _vazio()}
    try:
        caminhos = _arquivos(caminho_longo(pasta))
    except OSError:
        return {"erro": ERRO_LISTAR}
    limite = agora_s + TOLERANCIA_FUTURO_S
    melhor = None
    for caminho in caminhos:
        try:
            objeto = _objeto(caminho)
        except Exception:
            continue  # arquivo ilegível ou JSON quebrado: só ele é ignorado
        if not isinstance(objeto, dict):
            continue
        for lido, usage in candidatos(objeto):
            if lido / 1000 > limite:
                continue
            achada = barra(usage)
            if achada is None:
                continue
            if melhor is None or lido > melhor[0]:
                melhor = (lido, achada)
    if melhor is None:
        return {CHAVE: _vazio()}
    return {CHAVE: {"estado": "ok", "observado_epoch": melhor[0] / 1000, "barras": [melhor[1]]}}
