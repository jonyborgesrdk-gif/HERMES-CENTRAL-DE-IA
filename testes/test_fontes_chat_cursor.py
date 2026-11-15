"""Fonte do cursor-agent (E21, itens 24 a 27): último chat pelo updatedAtMs dos meta.json.

Só arquivos sintéticos em diretório temporário; o ctx sempre aponta cursor_chats_dir para
a pasta temporária e Path.home() é bloqueado, para nenhum teste cair no ~/.cursor real.
"""
import builtins
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lib.fontes import chat_cursor as mod

AGORA = 2_000_000_000
MARCAS = ("MARCA-TITULO-E21", "MARCA-PASTA-E21", "MARCA-PROMPT-E21")


def gravar(caminho, conteudo):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(conteudo, bytes):
        conteudo = json.dumps(conteudo).encode("utf-8")
    caminho.write_bytes(conteudo)
    return caminho


def retrato(raiz):
    """Nomes, bytes e mtime de tudo sob raiz (lido fora de qualquer espião)."""
    itens = {}
    for atual, pastas, arquivos in os.walk(raiz):
        for nome in pastas:
            itens[os.path.relpath(os.path.join(atual, nome), raiz)] = "pasta"
        for nome in arquivos:
            caminho = os.path.join(atual, nome)
            with open(caminho, "rb") as arquivo:
                dados = arquivo.read()
            itens[os.path.relpath(caminho, raiz)] = (dados, os.stat(caminho).st_mtime_ns)
    return itens


class Espiao:
    """Espia builtins.open, registrando caminho e modo, e delega ao open verdadeiro."""

    def __init__(self):
        self.abertos = []
        self._real = builtins.open

    def __call__(self, arquivo, *args, **kwargs):
        modo = args[0] if args else kwargs.get("mode", "r")
        self.abertos.append((str(arquivo), modo))
        return self._real(arquivo, *args, **kwargs)


@contextlib.contextmanager
def espiar_aberturas():
    """Registra toda abertura de arquivo, por qualquer grafia: open(), io.open() (por onde passam
    Path.open, Path.read_bytes e Path.read_text), os.open() e sqlite3.connect()."""
    abertos = []
    abrir, abrir_os, conectar = builtins.open, os.open, sqlite3.connect

    def espia(arquivo, *args, **kwargs):
        modo = args[0] if args else kwargs.get("mode", "r")
        abertos.append((arquivo if isinstance(arquivo, int) else os.fspath(arquivo), modo))
        return abrir(arquivo, *args, **kwargs)

    def espia_os(caminho, *args, **kwargs):
        abertos.append((os.fspath(caminho), "os.open"))
        return abrir_os(caminho, *args, **kwargs)

    def espia_sqlite(banco, *args, **kwargs):
        abertos.append((os.fspath(banco), "sqlite3.connect"))
        return conectar(banco, *args, **kwargs)

    with patch("builtins.open", espia), patch("io.open", espia), patch("os.open", espia_os), \
            patch("sqlite3.connect", espia_sqlite):
        yield abertos


class ChatCursorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.raiz = self.tmp / "chats"
        guarda = patch("pathlib.Path.home", side_effect=AssertionError("Path.home() real não pode ser usado"))
        guarda.start()
        self.addCleanup(guarda.stop)

    def ler(self, raiz=None):
        return mod.ler({"cursor_chats_dir": str(raiz or self.raiz), "agora": AGORA})

    def test_24_tres_meta_maior_updated_e_contagem(self):
        valores = [(AGORA - 3000) * 1000, (AGORA - 10) * 1000 + 123, (AGORA - 500) * 1000]
        for i, valor in enumerate(valores):
            gravar(self.raiz / f"hash{i}" / f"chat{i}" / "meta.json",
                   {"schemaVersion": 1, "createdAtMs": valor - 1000, "updatedAtMs": valor,
                    "hasConversation": True})
        saida = self.ler()
        self.assertEqual(saida, {mod.CHAVE: {"estado": "ok",
                                             "ultimo_chat_epoch": max(valores) / 1000.0,
                                             "chats": 3}})
        self.assertNotEqual(saida[mod.CHAVE]["ultimo_chat_epoch"], max(valores) // 1000)

    def test_25_ausente_sem_meta_e_invalidos_ignorados(self):
        sem_dado = {mod.CHAVE: {"estado": "sem_dado"}}
        # pasta ausente
        self.assertEqual(self.ler(self.tmp / "nao-existe"), sem_dado)
        # pasta sem meta.json
        sem_meta = self.tmp / "sem-meta"
        gravar(sem_meta / "h" / "c" / "outro.json", {"updatedAtMs": (AGORA - 100) * 1000})
        self.assertEqual(self.ler(sem_meta), sem_dado)

        self.assertEqual(mod.MAX_META, 65536)
        invalidos = {
            "truncado": b'{"updatedAtMs": 1999999000000',
            "profundo": b"[" * 30000 + b"]" * 30000,
            "grande": json.dumps({"updatedAtMs": (AGORA - 50) * 1000}).encode() + b" " * mod.MAX_META,
            "lista": b"[]",
            "numero": b"7",
            "nulo": b"null",
            "bool": {"updatedAtMs": True},
            "texto": {"updatedAtMs": str((AGORA - 60) * 1000)},
            "zero": {"updatedAtMs": 0},
            "negativo": {"updatedAtMs": -5},
            "futuro": {"updatedAtMs": (AGORA + 301) * 1000},
        }
        so_invalidos = self.tmp / "so-invalidos"
        com_valido = self.tmp / "com-valido"
        for raiz in (so_invalidos, com_valido):
            for nome, conteudo in invalidos.items():
                gravar(raiz / "h" / nome / "meta.json", conteudo)
        self.assertGreater((com_valido / "h" / "grande" / "meta.json").stat().st_size, mod.MAX_META)
        self.assertLessEqual((com_valido / "h" / "profundo" / "meta.json").stat().st_size, mod.MAX_META)
        gravar(com_valido / "h" / "valido" / "meta.json", {"updatedAtMs": (AGORA - 100) * 1000})

        # cada inválido é ignorado: só o válido conta, e nenhum inválido vira o maior
        self.assertEqual(self.ler(com_valido),
                         {mod.CHAVE: {"estado": "ok", "ultimo_chat_epoch": float(AGORA - 100), "chats": 1}})
        # só inválidos → sem_dado
        self.assertEqual(self.ler(so_invalidos), sem_dado)

    def test_26_limite_400_por_mtime_mais_novo(self):
        self.assertEqual(mod.MAX_ARQUIVOS, 400)
        self.assertEqual(mod.MAX_META, 65536)
        base_mtime = 1_700_000_000
        caminhos = []
        for i in range(401):
            # o mais antigo (i == 0) tem o maior updatedAtMs
            valor = (AGORA - 10) * 1000 if i == 0 else (AGORA - 100_000 + i) * 1000
            caminho = gravar(self.raiz / f"h{i // 100}" / f"c{i:03d}" / "meta.json", {"updatedAtMs": valor})
            os.utime(caminho, (base_mtime + i, base_mtime + i))
            caminhos.append(caminho)
        espiao = Espiao()
        with patch("builtins.open", side_effect=espiao):
            saida = self.ler()
        self.assertEqual(saida, {mod.CHAVE: {"estado": "ok",
                                             "ultimo_chat_epoch": float(AGORA - 100_000 + 400),
                                             "chats": 400}})
        lidos = {os.path.normcase(os.path.abspath(p)) for p, _ in espiao.abertos}
        self.assertEqual(len(espiao.abertos), 400)
        self.assertNotIn(os.path.normcase(os.path.abspath(caminhos[0])), lidos)
        self.assertEqual(lidos, {os.path.normcase(os.path.abspath(c)) for c in caminhos[1:]})

    def test_27_privacidade_e_so_leitura(self):
        pasta_chat = self.raiz / "a1b2c3" / "chat-1"
        gravar(pasta_chat / "meta.json",
               {"schemaVersion": 1, "createdAtMs": (AGORA - 900) * 1000, "updatedAtMs": (AGORA - 600) * 1000,
                "hasConversation": True, "title": "MARCA-TITULO-E21 conversa", "cwd": "C:\\MARCA-PASTA-E21\\proj"})
        gravar(pasta_chat / "store.db", b"SQLite format 3\x00 MARCA-PROMPT-E21")
        gravar(pasta_chat / "prompt_history.json", {"prompts": ["MARCA-PROMPT-E21"]})
        antes = retrato(self.tmp)
        with espiar_aberturas() as abertos:
            saida = self.ler()
        depois = retrato(self.tmp)

        self.assertEqual(saida, {mod.CHAVE: {"estado": "ok", "ultimo_chat_epoch": float(AGORA - 600), "chats": 1}})
        texto = json.dumps(saida, ensure_ascii=False)
        for marca in MARCAS:
            self.assertNotIn(marca, texto)
        self.assertNotIn("a1b2c3", texto)
        self.assertNotIn("chat-1", texto)

        # Fronteira de C7: toda abertura, por qualquer grafia, é de um meta.json e só para leitura.
        self.assertGreaterEqual(len(abertos), 1)
        for caminho, modo in abertos:
            self.assertEqual(Path(str(caminho)).name, "meta.json", caminho)
            self.assertIn("r", modo)
            self.assertTrue(set(modo) <= set("rbt"), modo)
        self.assertEqual(antes, depois)


if __name__ == "__main__":
    unittest.main()
