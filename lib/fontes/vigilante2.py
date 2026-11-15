"""Retrato do Vigilante 2 (opt-in, sem rede, sem subprocesso, só leitura).

Lê só ``%LOCALAPPDATA%\\vigilante2\\ultimo.json`` (no máximo 16 KiB) e devolve
apenas números, booleanos, ``None`` e textos de listas fixas. Nenhum texto do
arquivo é copiado para a saída.
"""
import json
import math
import os
from datetime import datetime, timezone

from . import agora

CHAVE = "vigilante2"
LIMITE_BYTES = 16 * 1024
PARADO_APOS_S = 180
TOLERANCIA_FUTURO_S = 300
MAX_CONTAGEM = 100000
TIPOS_ALERTA = ("fim_etapa", "aguarda_decisao", "confianca_pasta", "login", "cota_baixa", "terminal_caiu",
                "servico_fora", "painel_fora", "wire_fora", "falhas_repetidas", "outro")
TIPOS_TERMINAL = ("claude_code", "codex", "custom", "generic_shell", "outra")
CONTAGENS = ("online", "trabalhando", "atencao", "workspaces", "terminais", "falhas_seguidas")
ERRO_LER = "Vigilante 2: arquivo sem leitura"
ERRO_GRANDE = "Vigilante 2: arquivo grande demais"
ERRO_FORMATO = "Vigilante 2: formato inesperado"
DESLIGADO = {"estado": "desligado"}


def caminho_padrao(ambiente=None):
    ambiente = os.environ if ambiente is None else ambiente
    base = ambiente.get('LOCALAPPDATA')
    if not base:
        return None
    return os.path.join(base, 'vigilante2', 'ultimo.json')


def instante(valor):
    """Epoch de um texto ISO (sem fuso = UTC); qualquer outra coisa, None."""
    if not isinstance(valor, str):
        return None
    try:
        quando = datetime.fromisoformat(valor.replace('Z', '+00:00'))
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        epoch = quando.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    if not math.isfinite(epoch) or epoch <= 0:
        return None
    return epoch


def contagem(valor):
    if isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor <= MAX_CONTAGEM:
        return valor
    return None


def interpretar(objeto, agora_s):
    if not isinstance(objeto, dict):
        raise ValueError("objeto")
    schema = objeto.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != 1:
        raise ValueError("schema")
    gerado = instante(objeto.get("gerado_em"))
    if gerado is None or gerado > agora_s + TOLERANCIA_FUTURO_S:
        raise ValueError("gerado_em")
    wire_ok = objeto.get("wire_ok")
    painel_ok = objeto.get("painel_ok")
    if not isinstance(wire_ok, bool) or not isinstance(painel_ok, bool):
        raise ValueError("booleanos")
    numeros = {}
    for nome in CONTAGENS:
        valor = contagem(objeto.get(nome))
        if valor is None:
            raise ValueError(nome)
        numeros[nome] = valor
    bruto = objeto.get("por_tipo")
    if not isinstance(bruto, dict):
        raise ValueError("por_tipo")
    por_tipo = {}
    for tipo in TIPOS_TERMINAL:
        valor = contagem(bruto.get(tipo))
        por_tipo[tipo] = 0 if valor is None else valor
    for chave, valor in bruto.items():
        if chave in TIPOS_TERMINAL:
            continue
        valor = contagem(valor)
        if valor is not None:
            por_tipo["outra"] = min(por_tipo["outra"] + valor, MAX_CONTAGEM)
    itens = objeto.get("alertas_ativos")
    if not isinstance(itens, list):
        raise ValueError("alertas_ativos")
    menor = {}
    for item in itens:
        if not isinstance(item, dict) or not isinstance(item.get("tipo"), str):
            continue
        tipo = item["tipo"] if item["tipo"] in TIPOS_ALERTA else "outro"
        desde = instante(item.get("desde"))
        if desde is not None and desde > agora_s + TOLERANCIA_FUTURO_S:
            desde = None
        if tipo not in menor:
            menor[tipo] = desde
        elif desde is not None and (menor[tipo] is None or desde < menor[tipo]):
            menor[tipo] = desde
    alertas = [{"tipo": tipo, "desde_epoch": menor[tipo]} for tipo in TIPOS_ALERTA if tipo in menor]
    estado = "parado" if agora_s - gerado > PARADO_APOS_S else "ok"
    return {"estado": estado, "gerado_em_epoch": gerado, "wire_ok": wire_ok, "painel_ok": painel_ok,
            "online": numeros["online"], "trabalhando": numeros["trabalhando"],
            "atencao": numeros["atencao"], "workspaces": numeros["workspaces"],
            "terminais": numeros["terminais"], "falhas_seguidas": numeros["falhas_seguidas"],
            "por_tipo": por_tipo, "alertas": alertas}


def ler(ctx):
    caminho = ctx.get('vigilante_arquivo') or caminho_padrao()
    if caminho is None:
        return {CHAVE: dict(DESLIGADO)}
    try:
        with open(caminho, 'rb') as arquivo:
            dados = arquivo.read(LIMITE_BYTES + 1)
    except FileNotFoundError:
        return {CHAVE: dict(DESLIGADO)}
    except OSError:
        return {"erro": ERRO_LER}
    if len(dados) > LIMITE_BYTES:
        return {"erro": ERRO_GRANDE}
    try:
        texto = dados.decode('utf-8-sig', errors='replace')
        resultado = interpretar(json.loads(texto), agora(ctx))
    except (ValueError, RecursionError):
        return {"erro": ERRO_FORMATO}
    return {CHAVE: resultado}
