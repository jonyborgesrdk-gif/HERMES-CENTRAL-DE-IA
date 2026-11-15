"""Registro opt-in das fontes da E21 no coletor (itens 28 a 32 do §6): PAINEL_AGENTES_EXTRA.

Tudo por passada_viva com fontes e ctx injetados: nenhum teste lê ~/.grok, ~/.cursor ou o
state.db real do Hermes, nem roda o coletor de verdade. No item 32 as três fontes verdadeiras
leem só dados sintéticos em pasta temporária.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

from lib.fontes import sessoes_hermes as hermes

AGORA = 2_000_000_000
NOMES = ("sessoes_grok", "sessoes_hermes", "chat_cursor")
MARCAS = ("MARCA-PASTA-E21", "MARCA-TITULO-E21", "MARCA-PROMPT-E21", "MARCA-ARG-E21")
HOME = r"C:\Users\sintetico-e21"
AMBIENTE = {"USERPROFILE": HOME, "LOCALAPPDATA": HOME + r"\AppData\Local",
            "ProgramFiles": r"C:\ProgramasSinteticos-e21"}
GROK_EXE = HOME + r"\.grok\bin\grok.exe"
PID_GROK = 987654
ID_GROK = "e21c0032-5a5a-4b4b-8c8c-0d0d0d0d0d0d"
GRUPO_GROK = "C--projetos-MARCA-PASTA-E21"
ID_HERMES = "sessao-hermes-E21-sintetica"
HASH_CURSOR, CHAT_CURSOR = "hash-E21-sintetico", "chat-E21-sintetico"
IDENTIFICADORES = (ID_GROK, ID_HERMES, HASH_CURSOR, CHAT_CURSOR, str(PID_GROK))
PASTA_LIVRE = r"C:\projetos\MARCA-PASTA-E21"
COLUNAS_HERMES = ("id", "source", "model", "started_at", "ended_at", "last_activity_at", "input_tokens",
                  "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
                  "message_count", "tool_call_count", "title", "cwd", "display_name",
                  "last_activity_description")


def iso(e):
    """Horário no formato do Grok: ISO de 24 caracteres com milissegundos e Z."""
    quando = datetime.fromtimestamp(e, timezone.utc)
    return quando.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (quando.microsecond // 1000)


def ev(tipo, atras, **campos):
    return dict({"ts": iso(AGORA - atras), "type": tipo}, **campos)


def escrever(caminho, valor):
    caminho.write_text(valor if isinstance(valor, str) else json.dumps(valor), encoding="utf-8")


def montar_grok(raiz):
    """active_sessions.json e a pasta da sessão, com as quatro marcas nos campos e arquivos de texto livre."""
    pasta = raiz / "sessions" / GRUPO_GROK / ID_GROK
    pasta.mkdir(parents=True)
    escrever(raiz / "active_sessions.json", [{"session_id": ID_GROK, "pid": PID_GROK, "cwd": PASTA_LIVRE,
                                             "opened_at": iso(AGORA - 3600)}])
    eventos = [ev("turn_started", 100, model_id="grok-4.7"),
               ev("tool_started", 40, tool_name="read_file",
                  arguments={"path": PASTA_LIVRE + r"\MARCA-ARG-E21"}),
               ev("tool_completed", 30, tool_name="read_file", output="MARCA-PROMPT-E21")]
    escrever(pasta / "events.jsonl", "".join(json.dumps(e) + "\n" for e in eventos))
    escrever(pasta / "usage.json", {"sessionId": ID_GROK, "session": {
        "inputTokens": 1500, "outputTokens": 300, "totalTokens": 1800, "primaryModelId": "grok-4.7"},
        "turns": [{"prompt": "MARCA-PROMPT-E21", "title": "MARCA-TITULO-E21"}]})
    escrever(pasta / "signals.json", {"contextTokensUsed": 50000, "contextWindowTokens": 200000,
                                      "toolCallCount": 7, "primaryModelId": "grok-4.7", "cwd": PASTA_LIVRE})
    for nome in ("summary.json", "chat_history.jsonl", "prompt_history.jsonl", "prompt_context.json"):
        escrever(pasta / nome, {"cwd": PASTA_LIVRE, "generated_title": "MARCA-TITULO-E21",
                                "prompt": "MARCA-PROMPT-E21", "args": "MARCA-ARG-E21"})
    (pasta / "terminal").mkdir()
    escrever(pasta / "terminal" / "1.log", "MARCA-ARG-E21")


def criar_banco_hermes(caminho):
    """sessions com as 13 colunas usadas e quatro de texto livre; messages com content."""
    caminho.parent.mkdir(parents=True)
    linha = {"id": ID_HERMES, "source": "whatsapp", "model": "claude-opus-5-5",
             "started_at": AGORA - 3600.0, "ended_at": None, "last_activity_at": AGORA - 60.0,
             "input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 50000,
             "cache_write_tokens": 300, "reasoning_tokens": 40, "message_count": 12, "tool_call_count": 5,
             "title": "MARCA-TITULO-E21", "cwd": PASTA_LIVRE, "display_name": "MARCA-TITULO-E21",
             "last_activity_description": "MARCA-PROMPT-E21"}
    with closing(sqlite3.connect(caminho)) as db:
        db.execute("CREATE TABLE sessions (" + ", ".join(COLUNAS_HERMES) + ")")
        db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id, tool_name, timestamp, content)")
        db.execute("CREATE INDEX idx_messages_session ON messages(session_id, timestamp)")
        db.execute("INSERT INTO sessions (" + ", ".join(COLUNAS_HERMES) + ") VALUES ("
                   + ", ".join("?" for _ in COLUNAS_HERMES) + ")", [linha[c] for c in COLUNAS_HERMES])
        db.executemany("INSERT INTO messages (session_id, tool_name, timestamp, content) VALUES (?, ?, ?, ?)",
                       [(ID_HERMES, None, AGORA - 90.0, "MARCA-PROMPT-E21"),
                        (ID_HERMES, "terminal", AGORA - 60.0, "MARCA-ARG-E21")])
        db.commit()
    return caminho


def montar_cursor(raiz):
    """Um meta.json com título e pasta marcados; store.db e prompt_history.json ao lado."""
    pasta = raiz / HASH_CURSOR / CHAT_CURSOR
    pasta.mkdir(parents=True)
    escrever(pasta / "meta.json", {"schemaVersion": 1, "createdAtMs": (AGORA - 7200) * 1000,
                                   "updatedAtMs": (AGORA - 600) * 1000, "hasConversation": True,
                                   "title": "MARCA-TITULO-E21", "cwd": PASTA_LIVRE})
    (pasta / "store.db").write_bytes(b"MARCA-PROMPT-E21")
    escrever(pasta / "prompt_history.json", ["MARCA-PROMPT-E21"])
    return raiz


class IntegracaoAgentesExtraTests(unittest.TestCase):
    VARIAVEIS = ("PAINEL_AGENTES_EXTRA", "PAINEL_ERROS", "PAINEL_VIGILANTE", "PAINEL_COTA_CODEX_LOCAL",
                 "PAINEL_COTA_GROK_BOT", "PAINEL_COTA_CURSOR", "PAINEL_PRESENCA_LINUX", "PAINEL_PRESENCA",
                 "PAINEL_GASTOS")

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_agentes_extra', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.gravar_real = self.coletor.gravar
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.dados = {"sessoes_grok": {"estado": "ok", "sessoes": [{"chave": "a1b2c3d4e5", "estado": "ocioso"}]},
                      "sessoes_hermes": {"estado": "ok", "sessoes": [{"chave": "c3d4e5f6a7", "estado": "ativa"}]},
                      "chat_cursor": {"estado": "ok", "ultimo_chat_epoch": 1000.0, "chats": 1}}
        self.respostas = {nome: {nome: self.dados[nome]} for nome in NOMES}
        self.chamadas = {nome: [] for nome in NOMES}
        self.fontes = {nome: self.falsa(nome) for nome in NOMES}
        for nome in ('gravar', 'montar'):
            guarda = (patch.object(self.coletor, nome, side_effect=lambda *a: {'agentes': []})
                      if nome == 'montar' else patch.object(self.coletor, nome, return_value=None))
            guarda.start()
            self.addCleanup(guarda.stop)
        ambiente = patch.dict(os.environ)
        ambiente.start()
        self.addCleanup(ambiente.stop)
        self.limpar()

    def falsa(self, nome):
        def ler(ctx):
            self.chamadas[nome].append(ctx["agora"])
            return self.respostas[nome]
        return Mock(side_effect=ler)

    def limpar(self):
        for variavel in self.VARIAVEIS:
            os.environ.pop(variavel, None)

    def ligar(self, **valores):
        self.limpar()
        os.environ.update(valores)

    def passada(self, hora):
        return self.coletor.passada_viva(self.args, fontes=self.fontes,
                                        ctx={'agora': hora}, cache=self.cache)

    # 28
    def test_28_sem_valor_exato_1_nao_registra_nao_le_nao_publica(self):
        base = self.coletor.passada_viva(self.args, fontes={}, ctx={'agora': 1000}, cache={})
        for valor in (None, '0', 'true', 'sim'):
            with self.subTest(valor=valor), patch.dict(os.environ):
                self.ligar(**({} if valor is None else {'PAINEL_AGENTES_EXTRA': valor}))
                self.cache.clear()
                extras = self.coletor.extras_ativos()
                registradas = self.coletor.fontes_vivas(True)
                d = self.passada(1000)
                for nome in NOMES:
                    self.assertNotIn(nome, extras)
                    self.assertNotIn(nome, registradas)
                    self.assertNotIn(nome, d)
                    self.assertNotIn(nome, d['fontes'])
                    self.fontes[nome].assert_not_called()
                self.assertEqual(d, base)

    # 29
    def test_29_com_1_registra_e_publica_as_tres_com_ok(self):
        self.ligar(PAINEL_AGENTES_EXTRA='1')
        self.assertEqual(self.coletor.extras_ativos(), NOMES)
        registradas = self.coletor.fontes_vivas(True)
        for nome in NOMES:
            self.assertIn(nome, registradas)
            self.assertEqual(registradas[nome].__module__, 'fontes.' + nome)
            self.assertEqual(registradas[nome].__name__, 'ler')
        d = self.passada(1000)
        for nome in NOMES:
            with self.subTest(nome=nome):
                self.assertEqual(d[nome], self.dados[nome])
                self.assertIs(d['fontes'][nome]['ok'], True)
                self.assertEqual(self.chamadas[nome], [1000])

    # 30
    def test_30_cadencias_10_30_e_120_s(self):
        self.ligar(PAINEL_AGENTES_EXTRA='1')
        for hora in (1000, 1005, 1011, 1020, 1031, 1100, 1121):
            self.passada(hora)
        grok, hermes_, cursor = (self.chamadas[nome] for nome in NOMES)
        self.assertIn(1000, grok)
        self.assertIn(1011, grok)
        self.assertNotIn(1005, grok)
        self.assertIn(1000, hermes_)
        self.assertIn(1031, hermes_)
        self.assertNotIn(1020, hermes_)
        self.assertIn(1000, cursor)
        self.assertIn(1121, cursor)
        self.assertNotIn(1100, cursor)
        self.assertEqual(grok, [1000, 1011, 1031, 1100, 1121])
        self.assertEqual(hermes_, [1000, 1031, 1100])
        self.assertEqual(cursor, [1000, 1121])

    # 31
    def test_31_hermes_retem_ate_300s_e_as_outras_seguem_ok(self):
        self.ligar(PAINEL_AGENTES_EXTRA='1')
        d = self.passada(1000)
        self.assertEqual(d['sessoes_hermes'], self.dados['sessoes_hermes'])
        self.assertIs(d['fontes']['sessoes_hermes']['ok'], True)
        self.respostas['sessoes_hermes'] = {'erro': hermes.ERRO_BANCO}
        for hora in (1031, 1062):
            with self.subTest(hora=hora):
                d = self.passada(hora)
                self.assertEqual(self.chamadas['sessoes_hermes'][-1], hora)
                self.assertEqual(d['sessoes_hermes'], self.dados['sessoes_hermes'])
                self.assertIs(d['fontes']['sessoes_hermes']['ok'], False)
                self.assertIs(d['fontes']['sessoes_hermes']['desatualizado'], True)
                for nome in ('sessoes_grok', 'chat_cursor'):
                    self.assertEqual(d[nome], self.dados[nome])
                    self.assertIs(d['fontes'][nome]['ok'], True)
        d = self.passada(1301)
        self.assertEqual(self.chamadas['sessoes_hermes'][-1], 1301)
        self.assertEqual(d['sessoes_hermes'], {"estado": "erro"})
        self.assertIs(d['fontes']['sessoes_hermes']['desatualizado'], False)
        for nome in ('sessoes_grok', 'chat_cursor'):
            self.assertEqual(d[nome], self.dados[nome])
            self.assertIs(d['fontes'][nome]['ok'], True)

    # 32
    def test_32_passada_com_as_tres_fontes_reais_nao_publica_texto_da_origem(self):
        from lib.fontes import sessoes_grok, sessoes_hermes, chat_cursor
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_saida = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_saida.cleanup)
        raiz, saida = Path(tmp.name) / "origem", Path(tmp_saida.name)
        montar_grok(raiz / "grok")
        db = criar_banco_hermes(raiz / "hermes" / "state.db")
        chats = montar_cursor(raiz / "cursor" / "chats")
        entrada = b"".join(p.read_bytes() for p in raiz.rglob("*") if p.is_file())
        for marca in MARCAS:
            self.assertIn(marca.encode("utf-8"), entrada)

        processos = [{"pid": PID_GROK, "ppid": 1, "nome": "grok.exe", "caminho": GROK_EXE, "criado": AGORA - 5000}]
        ctx = {"grok_dir": str(raiz / "grok"), "hermes_db": str(db), "cursor_chats_dir": str(chats),
               "listar_processos": lambda predicado: [p for p in processos if predicado(p["nome"])],
               "ambiente": dict(AMBIENTE), "agora": AGORA}
        fontes = {"sessoes_grok": sessoes_grok.ler, "sessoes_hermes": sessoes_hermes.ler,
                  "chat_cursor": chat_cursor.ler}
        self.args.saida = str(saida)
        gravar = self.coletor.gravar
        gravar.side_effect = self.gravar_real
        with patch.dict(os.environ, {"PAINEL_AGENTES_EXTRA": "1",
                                     "LOCALAPPDATA": str(raiz / "localappdata")}):
            d = self.coletor.passada_viva(self.args, fontes=fontes, ctx=ctx, cache=self.cache)

        self.assertEqual(gravar.call_count, 1)
        entregue = gravar.call_args.args[0]
        self.assertIs(entregue, d)
        self.assertEqual(len(entregue["sessoes_grok"]["sessoes"]), 1)
        self.assertEqual(len(entregue["sessoes_hermes"]["sessoes"]), 1)
        self.assertEqual(entregue["chat_cursor"]["estado"], "ok")
        self.assertEqual(entregue["sessoes_grok"]["sessoes"][0]["chave"],
                         hashlib.sha256(("grok:" + ID_GROK).encode("utf-8")).hexdigest()[:10])
        self.assertEqual(entregue["sessoes_hermes"]["sessoes"][0]["chave"],
                         hashlib.sha256(("hermes:" + ID_HERMES).encode("utf-8")).hexdigest()[:10])
        for nome in NOMES:
            self.assertIs(entregue["fontes"][nome]["ok"], True)

        textos = {"objeto": json.dumps(entregue),
                  "objeto sem escape": json.dumps(entregue, ensure_ascii=False),
                  "dados.json": (saida / "dados.json").read_text(encoding="utf-8"),
                  "dados.js": (saida / "dados.js").read_text(encoding="utf-8")}
        caminho = str(raiz)
        proibidos = MARCAS + IDENTIFICADORES + (caminho, json.dumps(caminho)[1:-1], raiz.as_posix(),
                                                Path(tmp.name).name)
        for arquivo, texto in textos.items():
            for proibido in proibidos:
                with self.subTest(arquivo=arquivo, proibido=proibido):
                    self.assertNotIn(proibido, texto)


if __name__ == "__main__":
    unittest.main()
