"""Uso de rede por programa (E22): fonte sem privilégio que lê só o resumo da captura.

Lê só ``rede-resumo.json`` no diretório de dados do painel (no máximo 16 KiB), só para leitura, sem rede
e sem subprocesso, e nunca levanta. O resumo é gravado pelo processo elevado ``painel-rede capturar``
numa pasta do usuário, então qualquer processo do usuário pode trocá-lo: tudo é validado contra listas
fixas antes de sair. Saem só números finitos e dentro da faixa, booleanos, ``None`` e valores de lista
fixa; ``rotulo``, ``familia`` e ``tipo`` vêm da tabela fixa da E16, nunca do arquivo, e nenhuma outra
chave do arquivo é copiada. A saída sempre serializa com ``allow_nan=False``.
"""
import json
import math
import os
from pathlib import Path

from . import agora, diretorio
from . import presenca_processos

CHAVE = "rede_uso"
LIMITE_BYTES = 16 * 1024
PARADO_APOS_S = 30
TOLERANCIA_FUTURO_S = 300
MAX_BYTES = 10 ** 15
MAX_CONEXOES = 100000
MAX_EVENTOS_S = 10 ** 7
MAX_CPU_PCT = 6400.0
MAX_EPOCH = 10 ** 11
CATEGORIAS = ("local", "privada", "tailnet", "internet")
MOTIVOS = ("sessao", "provedor", "consumidor")
ERRO_LER = "rede: resumo sem leitura"
ERRO_GRANDE = "rede: resumo grande demais"
ERRO_FORMATO = "rede: formato inesperado"
DESLIGADO = {"estado": "desligado"}

ARQ_RESUMO = "rede-resumo.json"
ESTADOS = ("ativo", "pausado_carga", "encerrado", "erro")
JANELA_S = 60
_TABELA = {linha["produto"]: linha for linha in presenca_processos.PRODUTOS}


def numero(v, minimo, maximo):
    """``v`` se é ``int`` (nunca ``bool``) ou ``float`` finito dentro da faixa; senão ``None``.

    O inteiro é comparado direto, sem ``math.isfinite``: um inteiro enorme, que o ``json`` aceita, faria
    ``math.isfinite`` levantar ``OverflowError`` (M11)."""
    if type(v) is int:
        return v if minimo <= v <= maximo else None
    if type(v) is float:
        return v if math.isfinite(v) and minimo <= v <= maximo else None
    return None


def inteiro(v, maximo):
    return v if type(v) is int and 0 <= v <= maximo else None


def epoch_valido(v):
    return numero(v, 1, MAX_EPOCH)


def _par(v):
    if type(v) is list and len(v) == 2:
        enviados, recebidos = inteiro(v[0], MAX_BYTES), inteiro(v[1], MAX_BYTES)
        if enviados is not None and recebidos is not None:
            return [enviados, recebidos]
    return [0, 0]


def _categorias(v):
    origem = v if type(v) is dict else {}
    return {categoria: _par(origem.get(categoria)) for categoria in CATEGORIAS}


def _bloco(v):
    origem = v if type(v) is dict else {}
    conexoes = inteiro(origem.get("conexoes"), MAX_CONEXOES)
    return {"conexoes": 0 if conexoes is None else conexoes,
            "janela": _categorias(origem.get("janela")),
            "total": _categorias(origem.get("total"))}


def _programas(v):
    if type(v) is not list:
        return []
    saida, vistos = [], set()
    for item in v:
        if type(item) is not dict:
            continue
        produto = item.get("produto")
        if type(produto) is not str or produto not in _TABELA or produto in vistos:
            continue
        vistos.add(produto)
        linha = _TABELA[produto]
        programa = {"produto": linha["produto"], "rotulo": linha["rotulo"], "familia": linha["familia"],
                    "tipo": linha["tipo"]}
        programa.update(_bloco(item))
        saida.append(programa)
    return saida


def interpretar(objeto, agora_s):
    if type(objeto) is not dict:
        return {"erro": ERRO_FORMATO}
    schema = objeto.get("schema")
    estado = objeto.get("estado")
    gerado = epoch_valido(objeto.get("gerado_em"))
    if (type(schema) is not int or schema != 1 or type(estado) is not str or estado not in ESTADOS
            or gerado is None or gerado > agora_s + TOLERANCIA_FUTURO_S):
        return {"erro": ERRO_FORMATO}
    if estado == "erro":
        motivo = objeto.get("motivo")
        return {CHAVE: {"estado": "erro_captura",
                        "motivo": motivo if type(motivo) is str and motivo in MOTIVOS else "outro"}}
    if estado == "encerrado":
        return {CHAVE: {"estado": "encerrado", "gerado_em_epoch": gerado}}
    if agora_s - gerado > PARADO_APOS_S:
        return {CHAVE: {"estado": "parado", "ultimo_sinal_epoch": gerado}}
    perdidos = inteiro(objeto.get("perdidos"), MAX_BYTES)
    return {CHAVE: {
        "estado": estado,
        "gerado_em_epoch": gerado,
        "desde_epoch": epoch_valido(objeto.get("desde")),
        "janela_s": JANELA_S,
        "cpu_pct": numero(objeto.get("cpu_pct"), 0, MAX_CPU_PCT),
        "eventos_s": inteiro(objeto.get("eventos_s"), MAX_EVENTOS_S),
        "perdidos": 0 if perdidos is None else perdidos,
        "retoma_em_epoch": epoch_valido(objeto.get("retoma_em")) if estado == "pausado_carga" else None,
        "conexoes_no_teto": objeto.get("conexoes_no_teto") is True,
        "programas": _programas(objeto.get("programas")),
        "outros": _bloco(objeto.get("outros")),
    }}


def ler(ctx):
    try:
        caminho = Path(ctx["rede_resumo"]) if "rede_resumo" in ctx else diretorio(ctx) / ARQ_RESUMO
        agora_s = agora(ctx)
    except Exception:
        return {"erro": ERRO_LER}
    try:
        tamanho = os.stat(caminho).st_size
    except FileNotFoundError:
        return {CHAVE: dict(DESLIGADO)}
    except (OSError, ValueError):
        return {"erro": ERRO_LER}
    if tamanho > LIMITE_BYTES:
        return {"erro": ERRO_GRANDE}
    try:
        with open(caminho, "rb") as arquivo:
            dados = arquivo.read(LIMITE_BYTES + 1)
    except (OSError, ValueError):
        return {"erro": ERRO_LER}
    if len(dados) > LIMITE_BYTES:
        return {"erro": ERRO_GRANDE}
    try:
        objeto = json.loads(dados.decode("utf-8"))
    except (ValueError, RecursionError):
        return {"erro": ERRO_FORMATO}
    try:
        return interpretar(objeto, agora_s)
    except Exception:
        return {"erro": ERRO_FORMATO}
