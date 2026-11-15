"""Sessões vivas do Grok CLI (E21): estado, modelo, tokens, contexto e categoria da última ferramenta.

Só leitura e só de quatro arquivos: active_sessions.json na raiz e, na pasta de cada sessão viva,
events.jsonl, usage.json e signals.json. Nada é gravado, criado, renomeado ou apagado; sem processo
filho e sem rede. Saem só números, booleanos, valores de lista fixa, o modelo filtrado e uma chave
de 10 hexadecimais por sessão; nunca o identificador da sessão, o PID, a pasta ou qualquer texto.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re

from . import agora, epoch, linhas_finais
from . import presenca_processos

CHAVE = "sessoes_grok"
ERRO_PLATAFORMA = "sessões do Grok: plataforma sem suporte"
ERRO_LISTAR = "sessões do Grok: falha ao listar processos"
ERRO_LER = "sessões do Grok: registro de sessões ilegível"
MAX_SESSOES = 20
MAX_ATIVAS = 262144
MAX_JSON = 1048576
JANELA_EVENTOS = 65536
MAX_TOKENS = 10 ** 13
MAX_CHAMADAS = 10 ** 9
ID_SESSAO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}")
EXECUTAVEIS = ("grok.exe", "agent.exe")
CONHECIDOS = ("turn_started", "turn_ended", "phase_changed", "permission_requested",
              "permission_resolved", "tool_started", "tool_completed", "first_token", "loop_started")
FERRAMENTAS = {"read_file": "leitura", "grep": "busca", "list_dir": "busca",
               "run_terminal_command": "terminal", "get_command_or_subagent_output": "terminal",
               "search_replace": "edicao", "write": "edicao", "spawn_subagent": "subagente"}
CATEGORIAS = ("leitura", "busca", "terminal", "edicao", "subagente", "computador", "web", "outra")
MODELO = re.compile(r"[a-z0-9][a-z0-9.:_-]{0,63}")
CAMPOS_TOKENS = (("entrada", "inputTokens"), ("saida", "outputTokens"),
                 ("cache_leitura", "cachedReadTokens"), ("cache_escrita", "cacheCreationTokens"),
                 ("raciocinio", "reasoningTokens"), ("total", "totalTokens"))


def chave(ferramenta, ident):
    return hashlib.sha256((ferramenta + ":" + ident).encode("utf-8")).hexdigest()[:10]


def modelo(nome):
    if not isinstance(nome, str) or not nome:
        return None
    return nome if MODELO.fullmatch(nome) else "outro modelo"


def inteiro(valor, maximo):
    return valor if type(valor) is int and 0 <= valor <= maximo else None


def instante(valor, agora_s):
    try:
        if isinstance(valor, str):
            e = epoch(valor)
        elif type(valor) in (int, float):
            e = float(valor)
        else:
            return None
    except (OverflowError, OSError, ValueError):
        return None
    return e if math.isfinite(e) and 0 < e <= agora_s + 300 else None


def _json(caminho, maximo):
    """JSON de até `maximo` bytes; grande demais ou inválido -> ValueError; OSError propaga."""
    if os.stat(caminho).st_size > maximo:
        raise ValueError("arquivo grande demais")
    with open(caminho, "rb") as arquivo:
        dados = arquivo.read(maximo + 1)
    if len(dados) > maximo:
        raise ValueError("arquivo grande demais")
    try:
        return json.loads(dados)
    except (ValueError, RecursionError):
        raise ValueError("JSON inválido") from None


def interpretar_eventos(linhas, agora_s, completo=False):
    """Estado pelo fim de events.jsonl; `completo` diz que o arquivo inteiro coube na janela."""
    ultimo_tipo = ultimo_ts = modelo_bruto = ultima = None
    pendente = False
    validas = 0
    for linha in linhas:
        if not isinstance(linha, dict):
            continue
        validas += 1
        tipo = linha.get("type")
        if not isinstance(tipo, str) or tipo not in CONHECIDOS:
            continue
        ultimo_tipo = tipo
        quando = instante(linha.get("ts"), agora_s)
        if quando is not None:
            ultimo_ts = quando
        if tipo == "turn_started":
            pendente = False
            bruto = linha.get("model_id")
            if isinstance(bruto, str):
                modelo_bruto = bruto
        elif tipo in ("turn_ended", "permission_resolved"):
            pendente = False
        elif tipo == "permission_requested":
            pendente = True
        elif tipo == "tool_started":
            nome = linha.get("tool_name")
            ultima = FERRAMENTAS.get(nome, "outra") if isinstance(nome, str) else "outra"
    if ultimo_tipo is None:
        estado = "ocioso" if completo and validas else "indeterminado"
    elif ultimo_tipo == "turn_ended":
        estado = "ocioso"
    elif pendente:
        estado = "esperando"
    else:
        estado = "trabalhando"
    return {"estado": estado, "modelo_bruto": modelo_bruto, "ultima_ferramenta": ultima,
            "atividade_epoch": ultimo_ts}


def tokens(uso):
    """Só os seis contadores de `session`; nenhum outro campo de `uso` é acessado."""
    s = uso.get("session") if isinstance(uso, dict) else None
    if not isinstance(s, dict):
        return None
    valores = {nome: inteiro(s.get(campo), MAX_TOKENS) for nome, campo in CAMPOS_TOKENS}
    if all(valor is None for valor in valores.values()):
        return None
    return valores


def contexto(sinais):
    if not isinstance(sinais, dict):
        return None
    u = inteiro(sinais.get("contextTokensUsed"), MAX_TOKENS)
    j = inteiro(sinais.get("contextWindowTokens"), MAX_TOKENS)
    if u is None or not j:
        return None
    return min(100.0, round(100.0 * u / j, 1))


def _pasta(raiz, ident):
    try:
        for candidata in sorted((raiz / "sessions").glob("*/" + ident)):
            if candidata.is_dir():
                return candidata
    except OSError:
        pass
    return None


def _dict_json(caminho):
    try:
        valor = _json(caminho, MAX_JSON)
    except (OSError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def _sessao(raiz, ident, aberta, agora_s):
    pasta = _pasta(raiz, ident)
    lista, completo = [], False
    if pasta is not None:
        eventos = pasta / "events.jsonl"
        try:
            completo = os.stat(eventos).st_size <= JANELA_EVENTOS
            lista = list(linhas_finais(eventos, JANELA_EVENTOS))
        except (OSError, RecursionError):
            lista, completo = [], False
    r = interpretar_eventos(lista, agora_s, completo)
    sinais = _dict_json(pasta / "signals.json") if pasta is not None else {}
    uso = _dict_json(pasta / "usage.json") if pasta is not None else {}
    sessao_uso = uso.get("session")
    if not isinstance(sessao_uso, dict):
        sessao_uso = {}
    return {
        "chave": chave("grok", ident),
        "ferramenta": "grok",
        "estado": r["estado"],
        "modelo": modelo(r["modelo_bruto"] or sinais.get("primaryModelId")
                         or sessao_uso.get("primaryModelId")),
        "contexto_pct": contexto(sinais),
        "tokens": tokens(uso),
        "chamadas": inteiro(sinais.get("toolCallCount"), MAX_CHAMADAS),
        "ultima_ferramenta": r["ultima_ferramenta"],
        "atividade_epoch": r["atividade_epoch"],
        "inicio_epoch": instante(aberta, agora_s) if isinstance(aberta, str) else None,
    }


def ler(ctx):
    raiz = Path(ctx["grok_dir"]) if "grok_dir" in ctx else Path.home() / ".grok"
    agora_s = agora(ctx)
    try:
        ativas = _json(raiz / "active_sessions.json", MAX_ATIVAS)
    except FileNotFoundError:
        return {CHAVE: {"estado": "sem_dado", "sessoes": []}}
    except (OSError, ValueError):
        return {"erro": ERRO_LER}
    if not isinstance(ativas, list):
        return {"erro": ERRO_LER}

    candidatas, vistas = [], set()
    for item in ativas:
        if not isinstance(item, dict):
            continue
        ident, pid = item.get("session_id"), item.get("pid")
        if not isinstance(ident, str) or not ID_SESSAO.fullmatch(ident):
            continue
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            continue
        if ident in vistas:
            continue
        vistas.add(ident)
        candidatas.append((ident, pid, item.get("opened_at")))
    if not candidatas:
        return {CHAVE: {"estado": "ok", "sessoes": []}}

    listar = ctx.get("listar_processos")
    if listar is None:
        if os.name != "nt":
            return {"erro": ERRO_PLATAFORMA}
        listar = presenca_processos.listar_windows
    tabela = presenca_processos.tabela_resolvida(ctx["ambiente"] if "ambiente" in ctx else os.environ)
    try:
        processos = listar(lambda nome: isinstance(nome, str) and nome.lower() in EXECUTAVEIS)
    except OSError:
        return {"erro": ERRO_LISTAR}
    vivos = set()
    for processo in processos:
        if not isinstance(processo, dict):
            continue
        caminho, pid = processo.get("caminho"), processo.get("pid")
        if (isinstance(caminho, str) and type(pid) is int
                and presenca_processos.classificar(caminho, tabela) == "grok_cli"):
            vivos.add(pid)

    itens = []
    for ident, pid, aberta in candidatas:
        if len(itens) >= MAX_SESSOES:
            break
        if pid in vivos:
            itens.append(_sessao(raiz, ident, aberta, agora_s))
    itens.sort(key=lambda i: (i["atividade_epoch"] is None, -(i["atividade_epoch"] or 0.0), i["chave"]))
    return {CHAVE: {"estado": "ok", "sessoes": itens}}
