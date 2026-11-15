"""Fonte do cursor-agent: data do último chat local, pelos arquivos meta.json.

Só arquivos chamados meta.json são abertos, só para leitura, e de cada um só o campo
updatedAtMs é acessado. Saem só números (ultimo_chat_epoch, chats) e o estado; nenhum
texto do arquivo nem nome de pasta é publicado. Nada é gravado, criado, renomeado ou
apagado; sem processo externo e sem rede. MAX_ARQUIVOS e MAX_META são lidos como globais
do módulo na hora da chamada.
"""
import json
import os
from pathlib import Path

from . import agora

CHAVE = "chat_cursor"
ERRO_LISTAR = "chat do cursor-agent: falha ao listar"
MAX_ARQUIVOS = 400
MAX_META = 65536


def _updated_ms(caminho, agora_s):
    """updatedAtMs válido de um meta.json, ou None."""
    try:
        with open(caminho, "rb") as arquivo:
            bruto = arquivo.read(MAX_META + 1)
    except OSError:
        return None
    if len(bruto) > MAX_META:
        return None
    try:
        doc = json.loads(bruto)
    except (ValueError, RecursionError):
        return None
    if not isinstance(doc, dict):
        return None
    valor = doc.get("updatedAtMs")
    if type(valor) is int and 0 < valor <= (agora_s + 300) * 1000:
        return valor
    return None


def ler(ctx):
    raiz = Path(ctx["cursor_chats_dir"]) if "cursor_chats_dir" in ctx else Path.home() / ".cursor" / "chats"
    agora_s = agora(ctx)
    if not raiz.is_dir():
        return {CHAVE: {"estado": "sem_dado"}}
    try:
        arquivos = list(raiz.glob("*/*/meta.json"))
    except OSError:
        return {"erro": ERRO_LISTAR}
    com_stat = []
    for caminho in arquivos:
        try:
            info = os.stat(caminho)
        except OSError:
            continue
        com_stat.append((info.st_mtime, info.st_size, caminho))
    com_stat.sort(key=lambda item: item[0], reverse=True)
    maior = None
    chats = 0
    for _mtime, tamanho, caminho in com_stat[:MAX_ARQUIVOS]:
        if tamanho > MAX_META:
            continue
        valor = _updated_ms(caminho, agora_s)
        if valor is None:
            continue
        chats += 1
        if maior is None or valor > maior:
            maior = valor
    if maior is None:
        return {CHAVE: {"estado": "sem_dado"}}
    return {CHAVE: {"estado": "ok", "ultimo_chat_epoch": maior / 1000.0, "chats": chats}}
