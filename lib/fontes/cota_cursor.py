"""Plano local do Cursor (opt-in, sem rede, sem cota).

Lê só as chaves de plano e de situação da assinatura da tabela ``ItemTable``
do banco ``state.vscdb`` do Cursor, sempre com ``mode=ro&immutable=1``: o
SQLite lê sem pedir trava, sem abrir os auxiliares do banco e sem gravar
nada, com o aplicativo aberto ou fechado. O que ainda está só no registro
de gravação do aplicativo não é visto até ele consolidar ou fechar. Os
valores saem por lista fixa; qualquer outro texto vira ``"outra"``.
"""
import os
import sqlite3
from pathlib import Path

CHAVE = "cota_cursor"
CHAVE_PLANO = "cursorAuth/stripeMembershipType"
CHAVE_ASSINATURA = "cursorAuth/stripeSubscriptionStatus"
PLANOS = ("free", "free_trial", "pro", "pro_plus", "ultra", "business", "team", "enterprise")
ASSINATURAS = ("active", "trialing", "past_due", "canceled", "unpaid", "incomplete",
               "incomplete_expired", "paused")
T_ESPERA_S = 1
ERRO_LER = "cota Cursor: cache local sem leitura"
VAZIO = {"estado": "sem_dado", "plano": None, "assinatura": None}


def caminho_padrao(ambiente=None):
    ambiente = os.environ if ambiente is None else ambiente
    appdata = ambiente.get('APPDATA')
    if not appdata:
        return None
    return os.path.join(appdata, 'Cursor', 'User', 'globalStorage', 'state.vscdb')


def enumerar(valor, lista):
    """Item de `lista` igual ao texto (sem espaços nas pontas); senão "outra"; None fica None."""
    if valor is None:
        return None
    if isinstance(valor, bytes):
        valor = valor.decode('utf-8', errors='replace')
    if not isinstance(valor, str):
        return "outra"
    texto = valor.strip()
    for item in lista:
        if texto == item:
            return item
    return "outra"


def ler(ctx):
    caminho = ctx.get('cursor_db') or caminho_padrao()
    if caminho is None or not os.path.isfile(caminho):
        return {CHAVE: dict(VAZIO)}
    try:
        # Sempre immutable: sem pedir trava, sem abrir -wal/-shm/-journal e sem gravar nada,
        # com o Cursor aberto ou fechado. Não vê o que ainda está só no -wal.
        uri = Path(caminho).resolve().as_uri() + '?mode=ro&immutable=1'
        con = sqlite3.connect(uri, uri=True, timeout=T_ESPERA_S)
        try:
            linhas = con.execute(
                "SELECT key, value FROM ItemTable WHERE key IN (?, ?)",
                (CHAVE_PLANO, CHAVE_ASSINATURA),
            ).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return {"erro": ERRO_LER}
    valores = {}
    for chave, valor in linhas:
        valores[chave] = valor
    plano = enumerar(valores.get(CHAVE_PLANO), PLANOS)
    assinatura = enumerar(valores.get(CHAVE_ASSINATURA), ASSINATURAS)
    if plano is None and assinatura is None:
        return {CHAVE: dict(VAZIO)}
    return {CHAVE: {"estado": "ok", "plano": plano, "assinatura": assinatura}}
