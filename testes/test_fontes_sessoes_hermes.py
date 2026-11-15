"""Fonte das sessões do Hermes (E21, itens 15 a 23 da spec).

Só bancos SQLite sintéticos em pasta temporária; todo ctx aponta para eles e LOCALAPPDATA é
trocado por uma pasta temporária, então nenhum teste abre o state.db real do Hermes.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lib.fontes import sessoes_hermes as mod

AGORA = 2_000_000_000.0
MARCAS = ("MARCA-PASTA-E21", "MARCA-TITULO-E21", "MARCA-PROMPT-E21", "MARCA-ARG-E21")
# Colunas sem tipo declarado: o valor fica gravado com a classe exata que o teste insere
# (um texto numérico continua TEXTO), o que o item 18 precisa.
COLUNAS = ("id", "source", "model", "started_at", "ended_at", "last_activity_at", "input_tokens",
           "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
           "message_count", "tool_call_count", "title", "cwd", "display_name", "last_activity_description")
CAMPOS = {"chave", "ferramenta", "estado", "origem", "modelo", "tokens", "mensagens", "chamadas",
          "ultima_ferramenta", "atividade_epoch", "inicio_epoch"}
CAMPOS_TOKENS = {"entrada", "saida", "cache_leitura", "cache_escrita", "raciocinio", "total"}


def chave(ident):
    return hashlib.sha256(("hermes:" + ident).encode("utf-8")).hexdigest()[:10]


def sessao(ident, **mudar):
    base = {"id": ident, "source": "cli", "model": "claude-opus-5-5", "started_at": AGORA - 3600,
            "ended_at": None, "last_activity_at": AGORA - 60, "input_tokens": 1000, "output_tokens": 200,
            "cache_read_tokens": 50000, "cache_write_tokens": 300, "reasoning_tokens": 40,
            "message_count": 12, "tool_call_count": 5, "title": "texto livre", "cwd": "C:\\projeto",
            "display_name": "nome", "last_activity_description": "descricao"}
    base.update(mudar)
    return base


def criar_banco(caminho, sessoes, mensagens, colunas=COLUNAS):
    """sessions com as 13 colunas usadas e mais quatro de texto livre; messages com content."""
    with closing(sqlite3.connect(caminho)) as db:
        db.execute("CREATE TABLE sessions (" + ", ".join(colunas) + ")")
        db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id, tool_name, timestamp, content)")
        db.execute("CREATE INDEX idx_messages_session ON messages(session_id, timestamp)")
        marcadores = ", ".join("?" for _ in colunas)
        for item in sessoes:
            db.execute("INSERT INTO sessions (" + ", ".join(colunas) + ") VALUES (" + marcadores + ")",
                       [item.get(c) for c in colunas])
        for item in mensagens:
            db.execute("INSERT INTO messages (session_id, tool_name, timestamp, content) VALUES (?, ?, ?, ?)",
                       item)
        db.commit()


def retrato_bytes(pasta):
    """{nome: bytes} de todos os arquivos da pasta do banco (o principal e os auxiliares)."""
    return {nome: (Path(pasta) / nome).read_bytes() for nome in sorted(os.listdir(pasta))}


def textos(valor):
    if isinstance(valor, dict):
        for k, v in valor.items():
            yield k
            yield from textos(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from textos(v)
    elif isinstance(valor, str):
        yield valor


class SessoesHermesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = self.dir / "state.db"
        ambiente = patch.dict(os.environ, {"LOCALAPPDATA": str(self.dir / "localappdata")})
        ambiente.start()
        self.addCleanup(ambiente.stop)

    def ler(self, db=None):
        return mod.ler({"hermes_db": str(db or self.db), "agora": AGORA})

    def sessoes(self, saida):
        self.assertEqual(set(saida), {mod.CHAVE})
        self.assertEqual(saida[mod.CHAVE]["estado"], "ok")
        return saida[mod.CHAVE]["sessoes"]

    def test_15_so_sessao_aberta_com_atividade_nos_ultimos_900_s(self):
        criar_banco(self.db, [
            sessao("s-ativa-60", last_activity_at=AGORA - 60),
            sessao("s-aberta-901", last_activity_at=AGORA - 901),
            sessao("s-sem-hora", last_activity_at=None),
            sessao("s-encerrada", ended_at=AGORA - 10, last_activity_at=AGORA - 10),
        ], [("s-ativa-60", "terminal", AGORA - 70, "saida"),
            ("s-aberta-901", "patch", AGORA - 902, "x"),
            ("s-encerrada", "web_search", AGORA - 11, "x")])
        saida = self.ler()
        self.assertEqual(saida, {mod.CHAVE: {"estado": "ok", "sessoes": [{
            "chave": chave("s-ativa-60"), "ferramenta": "hermes", "estado": "ativa", "origem": "cli",
            "modelo": "claude-opus-5-5",
            "tokens": {"entrada": 1000, "saida": 200, "cache_leitura": 50000, "cache_escrita": 300,
                       "raciocinio": 40, "total": None},
            "mensagens": 12, "chamadas": 5, "ultima_ferramenta": "terminal",
            "atividade_epoch": AGORA - 60, "inicio_epoch": AGORA - 3600}]}})
        item = saida[mod.CHAVE]["sessoes"][0]
        self.assertRegex(item["chave"], r"\A[0-9a-f]{10}\Z")
        self.assertEqual(item["estado"], "ativa")
        self.assertIsNone(item["tokens"]["total"])

    def test_16_origem_por_lista_fixa(self):
        origens = list(mod.ORIGENS) + ["matrix", None, 7]
        self.assertEqual(len(mod.ORIGENS), 8)
        criar_banco(self.db, [sessao("origem-%02d" % i, source=o, last_activity_at=AGORA - 10 - i)
                              for i, o in enumerate(origens)], [])
        obtido = {s["chave"]: s["origem"] for s in self.sessoes(self.ler())}
        esperado = {chave("origem-%02d" % i): (o if i < 8 else "outra") for i, o in enumerate(origens)}
        self.assertEqual(obtido, esperado)
        self.assertEqual(sorted(obtido[chave("origem-%02d" % i)] for i in range(8)), sorted(mod.ORIGENS))

    def test_17_ultima_ferramenta_por_timestamp_e_categoria(self):
        criar_banco(self.db, [sessao("s-a", last_activity_at=AGORA - 10),
                              sessao("s-b", last_activity_at=AGORA - 20),
                              sessao("s-c", last_activity_at=AGORA - 30),
                              sessao("s-d", last_activity_at=AGORA - 40),
                              sessao("s-e", last_activity_at=AGORA - 50)],
                    [("s-a", "read_file", AGORA - 300, "x"),
                     ("s-a", "patch", AGORA - 200, "x"),
                     ("s-a", None, AGORA - 100, "mensagem mais nova sem ferramenta"),
                     ("s-b", "ferramenta_nova", AGORA - 50, "x"),
                     ("s-c", None, AGORA - 40, "so texto"),
                     ("s-e", 5, AGORA - 60, "nome que nao e texto")])
        obtido = {s["chave"]: s["ultima_ferramenta"] for s in self.sessoes(self.ler())}
        self.assertEqual(obtido, {chave("s-a"): "edicao", chave("s-b"): "outra", chave("s-c"): None,
                                  chave("s-d"): None, chave("s-e"): "outra"})
        self.assertTrue(set(mod.FERRAMENTAS.values()) <= set(mod.CATEGORIAS))

    def test_18_valores_ruins_e_texto_na_hora_de_atividade(self):
        criar_banco(self.db, [
            sessao("s-1", last_activity_at=AGORA - 10, input_tokens=-5, output_tokens=None,
                   cache_read_tokens="abc", cache_write_tokens=10, reasoning_tokens=7),
            sessao("s-2", last_activity_at=AGORA - 20, input_tokens=-1, output_tokens=None,
                   cache_read_tokens="x", cache_write_tokens=10 ** 13 + 1, reasoning_tokens="7"),
            sessao("s-3", last_activity_at=AGORA - 30, model="Claude-Opus-5"),
            sessao("s-futuro", last_activity_at=AGORA + 301),
            sessao("s-texto-alto", last_activity_at="9999999999"),
            sessao("s-texto-2h", last_activity_at=str(AGORA - 7200)),
        ], [])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT typeof(last_activity_at) FROM sessions WHERE id IN "
                                        "('s-texto-alto', 's-texto-2h')").fetchall(), [("text",), ("text",)])
        itens = {s["chave"]: s for s in self.sessoes(self.ler())}
        self.assertEqual(set(itens), {chave("s-1"), chave("s-2"), chave("s-3")})
        self.assertEqual(itens[chave("s-1")]["tokens"], {"entrada": None, "saida": None, "cache_leitura": None,
                                                         "cache_escrita": 10, "raciocinio": 7, "total": None})
        self.assertIsNone(itens[chave("s-2")]["tokens"])
        self.assertEqual(itens[chave("s-3")]["modelo"], "outro modelo")

        # Texto na hora de atividade não ocupa lugar entre as 50.
        outro = self.dir / "cinquenta.db"
        validas = [sessao("valida-%02d" % i, last_activity_at=AGORA - 100 - i) for i in range(50)]
        criar_banco(outro, validas + [sessao("texto-alto", last_activity_at="9999999999"),
                                      sessao("texto-2h", last_activity_at=str(AGORA - 7200))], [])
        chaves = [s["chave"] for s in self.sessoes(self.ler(outro))]
        self.assertEqual(chaves, [chave("valida-%02d" % i) for i in range(50)])

    def test_19_banco_ausente_invalido_ou_sem_tabela_ou_coluna(self):
        ausente = self.dir / "nao-existe" / "state.db"
        with patch.object(sqlite3, "connect", wraps=sqlite3.connect) as espiao:
            self.assertEqual(self.ler(ausente), {mod.CHAVE: {"estado": "sem_dado", "sessoes": []}})
        espiao.assert_not_called()
        self.assertFalse(ausente.exists())
        self.assertFalse(ausente.parent.exists())

        lixo = self.dir / "lixo.db"
        conteudo = b"isto nao e um banco SQLite " * 200
        lixo.write_bytes(conteudo)
        self.assertEqual(self.ler(lixo), {"erro": mod.ERRO_BANCO})
        self.assertEqual(lixo.read_bytes(), conteudo)

        sem_tabela = self.dir / "sem-tabela.db"
        with closing(sqlite3.connect(sem_tabela)) as db:
            db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id, tool_name, timestamp, content)")
            db.commit()
        self.assertEqual(self.ler(sem_tabela), {"erro": mod.ERRO_BANCO})

        sem_coluna = self.dir / "sem-coluna.db"
        criar_banco(sem_coluna, [sessao("s-1")], [], colunas=tuple(c for c in COLUNAS if c != "tool_call_count"))
        self.assertEqual(self.ler(sem_coluna), {"erro": mod.ERRO_BANCO})

    def test_20_limite_de_50_sessoes_mais_recentes_primeiro(self):
        self.assertEqual(mod.MAX_SESSOES, 50)
        self.assertEqual(mod.LIMITE_ATIVIDADE_S, 900)
        self.assertRegex(mod.SQL_SESSOES, r"LIMIT 50$")
        self.assertRegex(mod.SQL_FERRAMENTA, r"LIMIT 1$")
        criar_banco(self.db, [sessao("sessao-%02d" % i, last_activity_at=AGORA - i) for i in reversed(range(60))],
                    [])
        itens = self.sessoes(self.ler())
        self.assertEqual(len(itens), 50)
        self.assertEqual([s["chave"] for s in itens], [chave("sessao-%02d" % i) for i in range(50)])
        self.assertEqual([s["atividade_epoch"] for s in itens], [AGORA - i for i in range(50)])

    def test_21_uma_abertura_so_leitura_sem_mudar_arquivo(self):
        def ler_espiado(db):
            """Espia sqlite3.connect só em volta de ler; confere uma chamada, uri=True e timeout=1."""
            with patch.object(sqlite3, "connect", wraps=sqlite3.connect) as espiao:
                itens = self.sessoes(self.ler(db))
            espiao.assert_called_once()
            args, kwargs = espiao.call_args
            self.assertEqual(len(args), 1)
            self.assertEqual(kwargs, {"uri": True, "timeout": 1})
            return itens, args[0]

        def em_wal(db):
            with closing(sqlite3.connect(db)) as conexao:
                self.assertEqual(conexao.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")

        imutavel = "?mode=ro&immutable=1"

        with self.subTest(caso="a: diário, sem auxiliares"):
            pasta = self.dir / "a"
            pasta.mkdir()
            db = pasta / "state.db"
            criar_banco(db, [sessao("s-1")], [("s-1", "read_file", AGORA - 70, "x")])
            with closing(sqlite3.connect(db)) as conexao:
                self.assertEqual(conexao.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            antes = retrato_bytes(pasta)
            self.assertEqual(sorted(antes), ["state.db"])
            itens, uri = ler_espiado(db)
            self.assertEqual([s["ultima_ferramenta"] for s in itens], ["leitura"])
            self.assertTrue(uri.endswith(imutavel))
            self.assertEqual(uri, db.resolve().as_uri() + imutavel)
            self.assertEqual(retrato_bytes(pasta), antes)

        with self.subTest(caso="b: WAL fechado de forma limpa"):
            pasta = self.dir / "b"
            pasta.mkdir()
            db = pasta / "state.db"
            criar_banco(db, [sessao("s-b")], [("s-b", "web_search", AGORA - 70, "x")])
            em_wal(db)
            antes = retrato_bytes(pasta)
            self.assertEqual(sorted(antes), ["state.db"])
            itens, uri = ler_espiado(db)
            self.assertEqual([(s["chave"], s["ultima_ferramenta"]) for s in itens], [(chave("s-b"), "web")])
            self.assertEqual(uri, db.resolve().as_uri() + imutavel)
            self.assertEqual(retrato_bytes(pasta), antes)

        with self.subTest(caso="c: WAL com escritora aberta"):
            pasta = self.dir / "c"
            pasta.mkdir()
            db = pasta / "state.db"
            criar_banco(db, [sessao("s-c", last_activity_at=AGORA - 5000)], [])
            escritora = sqlite3.connect(db, timeout=5)
            try:
                self.assertEqual(escritora.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
                escritora.execute("UPDATE sessions SET last_activity_at = ? WHERE id = ?", (AGORA - 30, "s-c"))
                escritora.commit()
                antes = retrato_bytes(pasta)
                self.assertEqual(sorted(antes), ["state.db", "state.db-shm", "state.db-wal"])
                itens, uri = ler_espiado(db)
                self.assertTrue(uri.endswith("?mode=ro"))
                self.assertNotIn("immutable", uri)
                self.assertEqual(uri, db.resolve().as_uri() + "?mode=ro")
                self.assertEqual([s["chave"] for s in itens], [chave("s-c")])
                depois = retrato_bytes(pasta)
                self.assertEqual(sorted(depois), sorted(antes))
                # Exceção declarada do C2 (decisão 2a, D18): como leitor WAL, a abertura mode=ro
                # grava a marca de leitura dentro do -shm que já existe. Nada mais pode mudar.
                self.assertLessEqual({nome for nome in antes if depois[nome] != antes[nome]},
                                     {"state.db-shm"})
                self.assertEqual(depois["state.db"], antes["state.db"])
                self.assertEqual(depois["state.db-wal"], antes["state.db-wal"])
            finally:
                escritora.close()

        with self.subTest(caso="d: WAL fechado com -wal vazio e sem -shm"):
            pasta = self.dir / "d"
            pasta.mkdir()
            db = pasta / "state.db"
            criar_banco(db, [sessao("s-d")], [])
            em_wal(db)
            Path(str(db) + "-wal").write_bytes(b"")
            antes = retrato_bytes(pasta)
            self.assertEqual(sorted(antes), ["state.db", "state.db-wal"])
            _, uri = ler_espiado(db)
            self.assertEqual(uri, db.resolve().as_uri() + imutavel)
            self.assertEqual(retrato_bytes(pasta), antes)

        for sql in (mod.SQL_SESSOES, mod.SQL_FERRAMENTA):
            self.assertTrue(sql.startswith("SELECT "))
            self.assertIsNone(re.search(r"\b(pragma|attach|insert|update|delete|create|begin|replace)\b", sql, re.I))

    def test_22_wal_leitura_enxerga_o_wal_sem_mudar_o_arquivo_principal(self):
        criar_banco(self.db, [sessao("s-wal", last_activity_at=AGORA - 5000)],
                    [("s-wal", "web_search", AGORA - 5000, "x")])
        escritora = sqlite3.connect(self.db, timeout=5)
        try:
            self.assertEqual(escritora.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            escritora.execute("PRAGMA wal_autocheckpoint=0")
            escritora.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            self.assertEqual(self.sessoes(self.ler()), [])
            principal_antes_da_escrita = self.db.read_bytes()
            escritora.execute("UPDATE sessions SET last_activity_at = ? WHERE id = ?", (AGORA - 30, "s-wal"))
            escritora.commit()
            wal = Path(str(self.db) + "-wal")
            self.assertGreater(wal.stat().st_size, 0)
            principal = self.db.read_bytes()
            self.assertEqual(principal, principal_antes_da_escrita)  # a atividade nova está só no -wal
            antes = retrato_bytes(self.dir)
            self.assertEqual(sorted(antes), ["state.db", "state.db-shm", "state.db-wal"])

            itens = self.sessoes(self.ler())
            self.assertEqual([(s["chave"], s["atividade_epoch"], s["ultima_ferramenta"]) for s in itens],
                             [(chave("s-wal"), AGORA - 30, "web")])
            self.assertEqual(self.db.read_bytes(), principal)
            depois = retrato_bytes(self.dir)
            self.assertEqual(sorted(depois), sorted(antes))
            # Só o -shm pode mudar (marca de leitura do leitor WAL); o principal e o -wal, não.
            self.assertLessEqual({nome for nome in antes if depois[nome] != antes[nome]}, {"state.db-shm"})
            self.assertEqual(depois["state.db-wal"], antes["state.db-wal"])

            escritora.execute("UPDATE sessions SET message_count = 99 WHERE id = ?", ("s-wal",))
            escritora.commit()
            self.assertEqual(escritora.execute("SELECT message_count FROM sessions WHERE id = ?",
                                               ("s-wal",)).fetchone()[0], 99)
        finally:
            escritora.close()

    def test_23_privacidade_sem_texto_livre_nem_id(self):
        ids = ("sessao-privada-0b7e2c9d", "sessao-privada-91f4aa03")
        criar_banco(self.db, [
            sessao(ids[0], last_activity_at=AGORA - 10, title="MARCA-TITULO-E21 titulo",
                   cwd="C:\\MARCA-PASTA-E21\\projeto", display_name="MARCA-TITULO-E21",
                   last_activity_description="MARCA-PASTA-E21 MARCA-TITULO-E21 MARCA-ARG-E21"),
            sessao(ids[1], last_activity_at=AGORA - 20, source="MARCA-PASTA-E21", model="MARCA-TITULO-E21",
                   title="MARCA-TITULO-E21", cwd="/home/MARCA-PASTA-E21", display_name="MARCA-TITULO-E21",
                   last_activity_description="MARCA-PROMPT-E21"),
        ], [(ids[0], "terminal", AGORA - 15, "MARCA-PROMPT-E21 prompt do dono"),
            (ids[0], None, AGORA - 11, "MARCA-PROMPT-E21 resposta"),
            (ids[1], "MARCA-ARG-E21", AGORA - 25, "MARCA-PROMPT-E21")])
        saida = self.ler()
        itens = self.sessoes(saida)
        self.assertEqual(len(itens), 2)
        texto = json.dumps(saida, ensure_ascii=False)
        for proibido in MARCAS + ids:
            self.assertNotIn(proibido, texto)
        for item in itens:
            self.assertEqual(set(item), CAMPOS)
            self.assertEqual(set(item["tokens"]), CAMPOS_TOKENS)
        self.assertEqual([(s["origem"], s["modelo"], s["ultima_ferramenta"]) for s in itens],
                         [("cli", "claude-opus-5-5", "terminal"), ("outra", "outro modelo", "outra")])
        permitidos = (CAMPOS | CAMPOS_TOKENS | set(mod.ORIGENS) | set(mod.CATEGORIAS) |
                      {mod.CHAVE, "estado", "sessoes", "ok", "ativa", "hermes", "outro modelo", "claude-opus-5-5",
                       chave(ids[0]), chave(ids[1])})
        self.assertEqual([t for t in textos(saida) if t not in permitidos], [])
        for sql in (mod.SQL_SESSOES, mod.SQL_FERRAMENTA):
            self.assertIsNone(re.search(r"\b(title|cwd|display_name|last_activity_description|content)\b", sql, re.I))


if __name__ == "__main__":
    unittest.main()
