"""Sessões do Hermes abertas e com atividade recente, lidas do banco local só para leitura.

Uma abertura por leitura (URI com mode=ro quando -wal e -shm existem, senão
mode=ro&immutable=1; uri=True, timeout=1) e só as duas consultas
SELECT abaixo, sem coluna de texto livre. Saem só números, valores de lista fixa, o modelo
filtrado e a chave de 10 hexadecimais; o identificador da sessão nunca sai. A fonte não
grava, cria, renomeia nem apaga arquivo, e não há rede. Exceção declarada (decisão 2a,
D18): com -wal e -shm presentes, o SQLite, como leitor WAL, grava a marca de leitura
dentro do -shm que já existe; o arquivo principal e o -wal não mudam e nada é criado.
"""
from contextlib import closing
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3

from . import agora, epoch

CHAVE = "sessoes_hermes"
ERRO_BANCO = "sessões do Hermes: banco sem leitura"
LIMITE_ATIVIDADE_S = 900
MAX_SESSOES = 50
MAX_TOKENS = 10 ** 13
MAX_CHAMADAS = 10 ** 9
CATEGORIAS = ("leitura", "busca", "terminal", "edicao", "subagente", "computador", "web", "outra")
ORIGENS = ("local", "subagent", "cli", "oneshot", "webhook", "desktop", "tool", "whatsapp")
FERRAMENTAS = {"terminal": "terminal", "process_manage": "terminal", "execute_code": "terminal",
               "read_file": "leitura", "skill_view": "leitura",
               "search_files": "busca", "session_search": "busca", "tool_search": "busca",
               "write_file": "edicao", "patch": "edicao", "delegate_task": "subagente",
               "computer_use": "computador", "vision_analyze": "computador",
               "browser_exec": "web", "web_search": "web", "web_extract": "web"}
SQL_SESSOES = ("SELECT id, source, model, started_at, last_activity_at, input_tokens, output_tokens, "
               "cache_read_tokens, cache_write_tokens, reasoning_tokens, message_count, tool_call_count "
               "FROM sessions WHERE ended_at IS NULL AND typeof(last_activity_at) IN ('real', 'integer') "
               "AND last_activity_at >= ? "
               "ORDER BY last_activity_at DESC LIMIT 50")
SQL_FERRAMENTA = ("SELECT tool_name FROM messages WHERE session_id = ? AND tool_name IS NOT NULL "
                  "ORDER BY timestamp DESC LIMIT 1")


def chave(ferramenta, ident):
    return hashlib.sha256((ferramenta + ":" + ident).encode("utf-8")).hexdigest()[:10]


def modelo(nome):
    if not isinstance(nome, str) or not nome:
        return None
    return nome if re.fullmatch(r"[a-z0-9][a-z0-9.:_-]{0,63}", nome) else "outro modelo"


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


def _item(linha, atividade, ferramenta, agora_s):
    (ident, origem, nome, inicio, _ultima, entrada, saida, cache_leitura, cache_escrita,
     raciocinio, mensagens, chamadas) = linha
    tokens = {"entrada": inteiro(entrada, MAX_TOKENS), "saida": inteiro(saida, MAX_TOKENS),
              "cache_leitura": inteiro(cache_leitura, MAX_TOKENS),
              "cache_escrita": inteiro(cache_escrita, MAX_TOKENS),
              "raciocinio": inteiro(raciocinio, MAX_TOKENS), "total": None}
    if all(tokens[k] is None for k in ("entrada", "saida", "cache_leitura", "cache_escrita", "raciocinio")):
        tokens = None
    if ferramenta is None:
        ultima = None
    elif isinstance(ferramenta[0], str):
        ultima = FERRAMENTAS.get(ferramenta[0], "outra")
    else:
        ultima = "outra"
    return {"chave": chave("hermes", ident), "ferramenta": "hermes", "estado": "ativa",
            "origem": origem if isinstance(origem, str) and origem in ORIGENS else "outra",
            "modelo": modelo(nome), "tokens": tokens,
            "mensagens": inteiro(mensagens, MAX_CHAMADAS), "chamadas": inteiro(chamadas, MAX_CHAMADAS),
            "ultima_ferramenta": ultima, "atividade_epoch": atividade,
            "inicio_epoch": instante(inicio, agora_s) if type(inicio) in (int, float) else None}


def ler(ctx):
    caminho = Path(ctx.get("hermes_db") or
                   Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "hermes" / "state.db")
    agora_s = agora(ctx)
    if not caminho.is_file():
        return {CHAVE: {"estado": "sem_dado", "sessoes": []}}
    piso = agora_s - LIMITE_ATIVIDADE_S
    achadas = []
    try:
        real = caminho.resolve()
        base = str(real)
        # D18: com o banco em WAL e o Hermes fechado, a abertura só com mode=ro deixa dois arquivos
        # auxiliares na pasta dele; por isso mode=ro só quando -wal e -shm já existem.
        modo = ("?mode=ro" if os.path.isfile(base + "-wal") and os.path.isfile(base + "-shm")
                else "?mode=ro&immutable=1")
        with closing(sqlite3.connect(real.as_uri() + modo, uri=True, timeout=1)) as db:
            linhas = db.execute(SQL_SESSOES, (piso,)).fetchall()
            for linha in linhas:
                if len(achadas) >= MAX_SESSOES:
                    break
                ident = linha[0]
                atividade = instante(linha[4], agora_s)
                # O typeof da consulta e esta conferência existem porque no SQLite um texto é
                # sempre maior que um número e furaria a janela de atividade.
                if not isinstance(ident, str) or not ident or atividade is None or atividade < piso:
                    continue
                ferramenta = db.execute(SQL_FERRAMENTA, (ident,)).fetchone()
                achadas.append((linha, atividade, ferramenta))
    except (sqlite3.Error, OSError):
        return {"erro": ERRO_BANCO}
    return {CHAVE: {"estado": "ok", "sessoes": [_item(linha, atividade, ferramenta, agora_s)
                                                 for linha, atividade, ferramenta in achadas]}}
