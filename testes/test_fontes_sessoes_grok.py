"""Fonte sessoes_grok (E21, itens 1 a 14 do §6): eventos, tokens, contexto, modelo, leitura, limites,
privacidade e só leitura.

Só pastas temporárias e dados sintéticos: nenhum teste lê ~/.grok real, usa o USERPROFILE ou o
LOCALAPPDATA reais nem lista processos reais (ctx sempre traz grok_dir, ambiente e listar_processos).
"""
import builtins
from datetime import datetime, timezone
import hashlib
import json
import os
import pathlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.fontes import sessoes_grok as mod

AGORA = 2_000_000_000
HOME = r"C:\Users\sintetico-e21"
AMBIENTE = {"USERPROFILE": HOME, "LOCALAPPDATA": HOME + r"\AppData\Local",
            "ProgramFiles": r"C:\ProgramasSinteticos-e21"}
GROK_EXE = HOME + r"\.grok\bin\grok.exe"
AGENT_EXE = HOME + r"\.grok\bin\agent.exe"
MARCAS = ("MARCA-PASTA-E21", "MARCA-TITULO-E21", "MARCA-PROMPT-E21", "MARCA-ARG-E21")
GRUPO = "C--projetos-MARCA-PASTA-E21"
PERMITIDOS = {"active_sessions.json", "events.jsonl", "usage.json", "signals.json"}


def iso(e):
    """Horário no formato do Grok: ISO de 24 caracteres com milissegundos e Z."""
    quando = datetime.fromtimestamp(e, timezone.utc)
    return quando.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (quando.microsecond // 1000)


def ev(tipo, atras, **campos):
    return dict({"ts": iso(AGORA - atras), "type": tipo}, **campos)


def ident(n):
    return "%08x-1111-2222-3333-444444444444" % n


def proc(pid, caminho=GROK_EXE, nome="grok.exe"):
    return {"pid": pid, "ppid": 1, "nome": nome, "caminho": caminho, "criado": AGORA - 5000}


def sha(id_sessao):
    return hashlib.sha256(("grok:" + id_sessao).encode("utf-8")).hexdigest()[:10]


def montar(raiz, sessoes):
    """Cria active_sessions.json e sessions/<agrupamento com MARCA-PASTA-E21>/<id>/ com os arquivos pedidos."""
    raiz = Path(raiz)
    raiz.mkdir(parents=True, exist_ok=True)
    ativas = []
    for s in sessoes:
        ativas.append({"session_id": s["id"], "pid": s["pid"], "cwd": r"C:\projetos\MARCA-PASTA-E21",
                       "opened_at": s.get("aberta", iso(AGORA - 3600))})
        if s.get("sem_pasta"):
            continue
        pasta = raiz / "sessions" / GRUPO / s["id"]
        pasta.mkdir(parents=True, exist_ok=True)
        for nome, campo in (("events.jsonl", "eventos"), ("usage.json", "uso"), ("signals.json", "sinais")):
            if campo not in s:
                continue
            valor = s[campo]
            if isinstance(valor, bytes):
                dados = valor
            elif nome == "events.jsonl":
                dados = "".join(json.dumps(linha) + "\n" for linha in valor).encode("utf-8")
            else:
                dados = json.dumps(valor).encode("utf-8")
            (pasta / nome).write_bytes(dados)
    (raiz / "active_sessions.json").write_text(json.dumps(ativas), encoding="utf-8")
    return raiz


class Listar:
    """`listar_processos` falso: devolve os processos cujo nome passa no predicado e conta as chamadas."""

    def __init__(self, processos):
        self.processos = processos
        self.chamadas = 0

    def __call__(self, predicado):
        self.chamadas += 1
        return [p for p in self.processos if predicado(p["nome"])]


class Explosivo:
    def _boom(self, *args):
        raise AssertionError("turns foi acessado")

    __iter__ = __len__ = __bool__ = __getitem__ = __contains__ = _boom


class Vigiado(dict):
    """dict que levanta quando uma das chaves proibidas é lida."""
    proibidas = ()

    def __getitem__(self, chave):
        if chave in self.proibidas:
            raise AssertionError("chave proibida lida: " + chave)
        return super().__getitem__(chave)

    def get(self, chave, padrao=None):
        if chave in self.proibidas:
            raise AssertionError("chave proibida lida: " + chave)
        return super().get(chave, padrao)


class UsoVigiado(Vigiado):
    proibidas = ("turns",)


class SessaoVigiada(Vigiado):
    proibidas = ("costUsdTicks", "modelUsage")


def retrato(raiz):
    saida = {}
    for atual, pastas, arquivos in os.walk(raiz):
        relativo = os.path.relpath(atual, raiz)
        saida[relativo] = sorted(pastas)
        for nome in arquivos:
            caminho = os.path.join(atual, nome)
            with open(caminho, "rb") as arquivo:
                saida[os.path.join(relativo, nome)] = (os.path.getsize(caminho), arquivo.read())
    return saida


class SessoesGrokTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def ler(self, raiz, listar):
        return mod.ler({"grok_dir": str(raiz), "agora": AGORA, "ambiente": dict(AMBIENTE),
                        "listar_processos": listar})

    def sessoes(self, raiz, listar):
        saida = self.ler(raiz, listar)
        self.assertEqual(set(saida), {mod.CHAVE})
        self.assertEqual(saida[mod.CHAVE]["estado"], "ok")
        return saida[mod.CHAVE]["sessoes"]

    # 1
    def test_01_eventos_trabalhando_modelo_ferramenta_e_hora(self):
        r = mod.interpretar_eventos([ev("turn_started", 100, model_id="grok-4.7"),
                                     ev("phase_changed", 60, phase="streaming"),
                                     ev("tool_started", 30, tool_name="read_file")], AGORA)
        self.assertEqual(r, {"estado": "trabalhando", "modelo_bruto": "grok-4.7",
                             "ultima_ferramenta": "leitura", "atividade_epoch": AGORA - 30})

    # 2
    def test_02_eventos_ocioso_esperando_e_permissao_resolvida(self):
        casos = (
            ([ev("turn_started", 50), ev("tool_started", 40, tool_name="grep"), ev("turn_ended", 30)], "ocioso"),
            ([ev("turn_started", 50), ev("permission_requested", 40, tool_name="write")], "esperando"),
            ([ev("turn_started", 50), ev("permission_requested", 40, tool_name="write"),
              ev("permission_resolved", 30, tool_name="write")], "trabalhando"),
            ([ev("turn_started", 50), ev("permission_requested", 40, tool_name="write"),
              ev("turn_ended", 30)], "ocioso"),
            ([ev("turn_started", 50), ev("permission_requested", 40, tool_name="write"),
              ev("first_token", 30)], "esperando"),
        )
        for linhas, estado in casos:
            with self.subTest(tipos=[linha["type"] for linha in linhas]):
                self.assertEqual(mod.interpretar_eventos(linhas, AGORA)["estado"], estado)

    # 3
    def test_03_eventos_desconhecidos_ferramenta_fora_da_tabela_e_hora_invalida(self):
        vazio = {"estado": "indeterminado", "modelo_bruto": None, "ultima_ferramenta": None,
                 "atividade_epoch": None}
        desconhecidos = [ev("mcp_config_resolved", 50), ev("mcp_init_completed", 40),
                         ev("user_message", 30, texto="MARCA-PROMPT-E21")]
        self.assertEqual(mod.interpretar_eventos([], AGORA), vazio)
        saida = mod.interpretar_eventos(desconhecidos, AGORA)
        self.assertEqual(saida, vazio)
        self.assertNotIn("MARCA-PROMPT-E21", json.dumps(saida))

        for linha in (ev("tool_started", 10, tool_name="nova_ferramenta"), ev("tool_started", 10, tool_name=7),
                      ev("tool_started", 10, tool_name=None), ev("tool_started", 10)):
            with self.subTest(tool_name=linha.get("tool_name", "ausente")):
                self.assertEqual(mod.interpretar_eventos([linha], AGORA)["ultima_ferramenta"], "outra")

        for ts in ("nao-e-data", iso(AGORA + 301), AGORA + 301, 10 ** 400, "1" + "0" * 400,
                   "0001-01-01T00:00:00", True, {}, [], None):
            with self.subTest(ts=repr(ts)[:40]):
                r = mod.interpretar_eventos([{"ts": ts, "type": "turn_ended"}], AGORA)
                self.assertIsNone(r["atividade_epoch"])
                self.assertEqual(r["estado"], "ocioso")
        self.assertEqual(mod.interpretar_eventos([{"ts": AGORA + 300, "type": "turn_ended"}], AGORA)
                         ["atividade_epoch"], AGORA + 300)

        self.assertEqual(mod.interpretar_eventos(desconhecidos, AGORA, completo=True)["estado"], "ocioso")
        self.assertEqual(mod.interpretar_eventos([], AGORA, completo=True)["estado"], "indeterminado")

    # 4
    def test_04_tokens_so_de_session_sem_turns_e_sem_custo(self):
        completo = {"inputTokens": 10, "outputTokens": 20, "cachedReadTokens": 30, "cacheCreationTokens": 40,
                    "reasoningTokens": 50, "totalTokens": 60, "modelCalls": 3, "costUsdTicks": 999,
                    "turnCount": 2, "primaryModelId": "grok-4.7", "modelUsage": {"grok-4.7": {"inputTokens": 10}}}
        esperado = {"entrada": 10, "saida": 20, "cache_leitura": 30, "cache_escrita": 40, "raciocinio": 50,
                    "total": 60}
        self.assertEqual(mod.tokens({"session": completo, "turns": [{"inputTokens": 5}]}), esperado)
        mapa = {"inputTokens": "entrada", "outputTokens": "saida", "cachedReadTokens": "cache_leitura",
                "cacheCreationTokens": "cache_escrita", "reasoningTokens": "raciocinio", "totalTokens": "total"}
        for campo, nome in mapa.items():
            for ruim in (-1, True, 1.5, "10", mod.MAX_TOKENS + 1):
                with self.subTest(campo=campo, valor=ruim):
                    self.assertEqual(mod.tokens({"session": dict(completo, **{campo: ruim})}),
                                     dict(esperado, **{nome: None}))
        for uso in ({}, {"session": None}, {"session": [1, 2]}, {"session": "texto"}, None, [], "texto"):
            with self.subTest(uso=uso):
                self.assertIsNone(mod.tokens(uso))

        uso = UsoVigiado({"session": dict(completo), "turns": Explosivo()})
        self.assertEqual(mod.tokens(uso), esperado)
        self.assertEqual(mod.tokens({"session": SessaoVigiada(completo)}), esperado)

    # 5
    def test_05_contexto(self):
        self.assertEqual(mod.contexto({"contextTokensUsed": 50000, "contextWindowTokens": 200000}), 25.0)
        for janela in (0, "200000", -5, True, 1.5, None):
            with self.subTest(janela=janela):
                self.assertIsNone(mod.contexto({"contextTokensUsed": 50000, "contextWindowTokens": janela}))
        self.assertIsNone(mod.contexto({"contextTokensUsed": 50000}))
        self.assertEqual(mod.contexto({"contextTokensUsed": 300000, "contextWindowTokens": 200000}), 100.0)

    # 6
    def test_06_modelo(self):
        self.assertEqual(mod.modelo("grok-4.7"), "grok-4.7")
        self.assertIsNone(mod.modelo(None))
        self.assertIsNone(mod.modelo(""))
        for nome in ("grok 4.7", "Grok-4.7", "x-ai/grok-4.7"):
            with self.subTest(nome=nome):
                self.assertEqual(mod.modelo(nome), "outro modelo")

    # 7
    def test_07_ler_duas_sessoes_vivas_com_todos_os_campos(self):
        id_a, id_b = ident(0xA1), ident(0xB2)
        raiz = montar(self.tmp / "grok", [
            {"id": id_b, "pid": 4102, "aberta": iso(AGORA - 9000),
             "eventos": [ev("turn_started", 7300), ev("turn_ended", 7200, outcome="completed")],
             "sinais": {"primaryModelId": "grok-code-fast-1", "toolCallCount": 3},
             "uso": {"session": {"totalTokens": 1234, "primaryModelId": "grok-uso"}}},
            {"id": id_a, "pid": 4101,
             "eventos": [ev("turn_started", 100, model_id="grok-4.7"),
                         ev("tool_started", 30, tool_name="run_terminal_command")],
             "sinais": {"contextTokensUsed": 50000, "contextWindowTokens": 200000, "toolCallCount": 34,
                        "primaryModelId": "grok-sinal"},
             "uso": {"sessionId": id_a, "updatedAt": iso(AGORA - 30),
                     "session": {"inputTokens": 1500000, "outputTokens": 500000, "cachedReadTokens": 1200000,
                                 "cacheCreationTokens": 0, "reasoningTokens": 20000, "totalTokens": 2000000,
                                 "modelCalls": 40, "costUsdTicks": 77, "turnCount": 5,
                                 "primaryModelId": "grok-velho"},
                     "turns": [{"inputTokens": 1}]}},
        ])
        itens = self.sessoes(raiz, Listar([proc(4101), proc(4102, AGENT_EXE, "agent.exe")]))
        self.assertEqual(itens, [
            {"chave": sha(id_a), "ferramenta": "grok", "estado": "trabalhando", "modelo": "grok-4.7",
             "contexto_pct": 25.0,
             "tokens": {"entrada": 1500000, "saida": 500000, "cache_leitura": 1200000, "cache_escrita": 0,
                        "raciocinio": 20000, "total": 2000000},
             "chamadas": 34, "ultima_ferramenta": "terminal", "atividade_epoch": AGORA - 30,
             "inicio_epoch": AGORA - 3600},
            {"chave": sha(id_b), "ferramenta": "grok", "estado": "ocioso", "modelo": "grok-code-fast-1",
             "contexto_pct": None,
             "tokens": {"entrada": None, "saida": None, "cache_leitura": None, "cache_escrita": None,
                        "raciocinio": None, "total": 1234},
             "chamadas": 3, "ultima_ferramenta": None, "atividade_epoch": AGORA - 7200,
             "inicio_epoch": AGORA - 9000},
        ])
        for item in itens:
            self.assertRegex(item["chave"], r"\A[0-9a-f]{10}\Z")

    # 8
    def test_08_ler_so_pid_de_processo_grok_cli(self):
        ids = [ident(n) for n in (0x801, 0x802, 0x803, 0x804, 0x805, 0x806)]
        raiz = montar(self.tmp / "grok", [{"id": i, "pid": 5001 + n, "eventos": [ev("turn_ended", 10)]}
                                          for n, i in enumerate(ids)])
        caminho_bot = HOME + r"\AppData\Local\Programs\Grok Bot\Grok Bot.exe"
        caminho_claude = HOME + r"\.local\bin\claude.exe"
        tabela = mod.presenca_processos.tabela_resolvida(dict(AMBIENTE))
        self.assertEqual(mod.presenca_processos.classificar(caminho_bot, tabela), "grok_bot")
        self.assertEqual(mod.presenca_processos.classificar(caminho_claude, tabela), "claude_cli")
        # Os dois ficam com o nome padrão grok.exe: passam no filtro por nome e quem os barra é a classificação do caminho.
        listar = Listar([proc(5001), proc(5003, r"C:\outro\grok.exe"), proc(5004, None),
                         proc(5005, caminho_bot), proc(5006, caminho_claude)])
        itens = self.sessoes(raiz, listar)
        self.assertEqual([item["chave"] for item in itens], [sha(ids[0])])
        self.assertEqual(listar.chamadas, 1)

    # 9
    def test_09_ler_registro_ausente_invalido_e_sem_candidatas(self):
        raiz = self.tmp / "grok"
        raiz.mkdir()
        listar = Listar([proc(5001)])
        self.assertEqual(self.ler(raiz, listar), {mod.CHAVE: {"estado": "sem_dado", "sessoes": []}})
        ativas = raiz / "active_sessions.json"
        valido = json.dumps([{"session_id": ident(0x901), "pid": 9001, "cwd": "x" * 80}]).encode()
        for nome, dados in (("inválido", b"{nao e json"), ("cortado", valido[:20]), ("não lista", b'{"a": 1}'),
                            ("aninhamento", b"[" * 50000 + b"]" * 50000)):
            with self.subTest(caso=nome):
                ativas.write_bytes(dados)
                self.assertEqual(self.ler(raiz, listar), {"erro": mod.ERRO_LER})
        ativas.write_bytes(valido)
        self.assertEqual(self.ler(raiz, Listar([]))[mod.CHAVE]["estado"], "ok")
        with patch.object(mod, "MAX_ATIVAS", len(valido) - 1):
            self.assertEqual(self.ler(raiz, listar), {"erro": mod.ERRO_LER})

        invalidos = [{"session_id": "aaaa..bbbbcccc", "pid": 5001}, {"session_id": "../aaaabbbbcccc", "pid": 5001},
                     {"session_id": "aaaabbbb/cccc", "pid": 5001}, {"session_id": ident(0x902), "pid": True},
                     "texto", 7]
        for nome, lista in (("vazia", []), ("só inválidos", invalidos)):
            with self.subTest(caso=nome):
                ativas.write_text(json.dumps(lista), encoding="utf-8")
                listar = Listar([proc(5001), proc(1)])
                self.assertEqual(self.ler(raiz, listar), {mod.CHAVE: {"estado": "ok", "sessoes": []}})
                self.assertEqual(listar.chamadas, 0)

    # 10
    def test_10_ler_falha_ao_listar_e_plataforma_sem_suporte(self):
        raiz = montar(self.tmp / "grok", [{"id": ident(0xA01), "pid": 7001}])

        def quebra(predicado):
            raise OSError("falha sintética")

        self.assertEqual(self.ler(raiz, quebra), {"erro": mod.ERRO_LISTAR})

        class OsPosix:
            """os.name == "posix" só para a fonte; trocar o os.name global quebra o pathlib no Windows."""
            name = "posix"

            def __getattr__(self, nome):
                return getattr(os, nome)

        with patch.object(mod, "os", OsPosix()):
            saida = mod.ler({"grok_dir": str(raiz), "agora": AGORA, "ambiente": dict(AMBIENTE)})
        self.assertEqual(saida, {"erro": mod.ERRO_PLATAFORMA})

    # 11
    def test_11_ler_arquivos_faltando_quebrados_e_limites(self):
        profunda = b"[" * 32000 + b"]" * 32000
        ids = {letra: ident(0xB00 + n) for n, letra in enumerate("abcdef")}
        raiz = montar(self.tmp / "um", [
            {"id": ids["a"], "pid": 8001, "sem_pasta": True},
            {"id": ids["b"], "pid": 8002},
            {"id": ids["c"], "pid": 8003, "eventos": [ev("turn_ended", 40)],
             "uso": b'{"session": {"inputTokens": 5', "sinais": {"toolCallCount": 2}},
            {"id": ids["d"], "pid": 8004, "eventos": [ev("turn_started", 60), ev("turn_ended", 50)],
             "sinais": [1, 2], "uso": {"session": {"inputTokens": 7, "primaryModelId": "grok-do-uso"}}},
            {"id": ids["e"], "pid": 8005,
             "eventos": (json.dumps(ev("turn_ended", 20)) + "\n").encode() + profunda + b"\n"},
            {"id": ids["f"], "pid": 8006,
             "eventos": [ev("mcp_config_resolved", 90), ev("mcp_server_starting", 80),
                         ev("mcp_server_connected", 70), ev("mcp_init_completed", 60)]},
        ])
        itens = {item["chave"]: item for item in
                 self.sessoes(raiz, Listar([proc(8001 + n) for n in range(6)]))}
        self.assertEqual(len(itens), 6)
        nulos = {"modelo": None, "contexto_pct": None, "tokens": None, "chamadas": None,
                 "ultima_ferramenta": None, "atividade_epoch": None}
        for letra in "ab":
            with self.subTest(sessao=letra):
                item = itens[sha(ids[letra])]
                self.assertEqual(item["estado"], "indeterminado")
                self.assertEqual({k: item[k] for k in nulos}, nulos)
                self.assertEqual(item["inicio_epoch"], AGORA - 3600)
        c = itens[sha(ids["c"])]
        self.assertEqual((c["estado"], c["tokens"], c["chamadas"], c["atividade_epoch"]),
                         ("ocioso", None, 2, AGORA - 40))
        d = itens[sha(ids["d"])]
        self.assertEqual((d["estado"], d["modelo"], d["contexto_pct"], d["chamadas"]),
                         ("ocioso", "grok-do-uso", None, None))
        self.assertEqual(d["tokens"], {"entrada": 7, "saida": None, "cache_leitura": None, "cache_escrita": None,
                                       "raciocinio": None, "total": None})
        e = itens[sha(ids["e"])]
        self.assertEqual((e["estado"], e["atividade_epoch"]), ("indeterminado", None))
        f = itens[sha(ids["f"])]
        self.assertEqual((f["estado"], f["atividade_epoch"]), ("ocioso", None))

        uso = {"session": {"inputTokens": 11}, "pad": ""}
        uso["pad"] = "x" * (300 - len(json.dumps(uso)))
        self.assertEqual(len(json.dumps(uso)), 300)
        raiz = montar(self.tmp / "dois", [{"id": ident(0xC01), "pid": 8101, "uso": uso}])
        listar = Listar([proc(8101)])
        self.assertEqual(self.sessoes(raiz, listar)[0]["tokens"]["entrada"], 11)
        with patch.object(mod, "MAX_JSON", 200):
            self.assertIsNone(self.sessoes(raiz, listar)[0]["tokens"])

        eventos = [ev("turn_started", 500, model_id="grok-4.7"), ev("turn_ended", 400)]
        eventos += [ev("mcp_server_connected", 300 - n, server="servidor-sintetico-%02d" % n) for n in range(10)]
        raiz = montar(self.tmp / "tres", [{"id": ident(0xC02), "pid": 8102, "eventos": eventos}])
        listar = Listar([proc(8102)])
        self.assertEqual(self.sessoes(raiz, listar)[0]["estado"], "ocioso")
        with patch.object(mod, "JANELA_EVENTOS", 400):
            item = self.sessoes(raiz, listar)[0]
        self.assertEqual((item["estado"], item["modelo"], item["atividade_epoch"]), ("indeterminado", None, None))

    # 12
    def test_12_ler_no_maximo_20_sessoes_e_id_repetido_uma_vez(self):
        self.assertEqual(mod.MAX_SESSOES, 20)
        self.assertEqual(mod.MAX_ATIVAS, 262144)
        self.assertEqual(mod.MAX_JSON, 1048576)
        self.assertEqual(mod.JANELA_EVENTOS, 65536)
        self.assertEqual(mod.MAX_TOKENS, 10 ** 13)
        self.assertEqual(mod.MAX_CHAMADAS, 10 ** 9)
        ids = [ident(0xD00 + n) for n in range(25)]
        raiz = montar(self.tmp / "muitas", [{"id": i, "pid": 9001 + n, "sem_pasta": True}
                                            for n, i in enumerate(ids)])
        itens = self.sessoes(raiz, Listar([proc(9001 + n) for n in range(25)]))
        self.assertEqual(len(itens), 20)
        self.assertEqual({item["chave"] for item in itens}, {sha(i) for i in ids[:20]})

        repetido = ident(0xD99)
        raiz = montar(self.tmp / "repetido", [{"id": repetido, "pid": 9101, "aberta": iso(AGORA - 100)},
                                              {"id": repetido, "pid": 9102, "aberta": iso(AGORA - 200)}])
        itens = self.sessoes(raiz, Listar([proc(9101), proc(9102)]))
        self.assertEqual([(item["chave"], item["inicio_epoch"]) for item in itens], [(sha(repetido), AGORA - 100)])

    # 13
    def test_13_privacidade_nenhum_texto_de_sessao_na_saida(self):
        id_sessao, pid = ident(0xE13), 987651
        raiz = montar(self.tmp / "grok", [{
            "id": id_sessao, "pid": pid,
            "eventos": [ev("turn_started", 100, model_id="grok-4.7"),
                        ev("tool_started", 30, tool_name="run_terminal_command",
                           arguments="MARCA-ARG-E21 --apagar C:\\projetos\\MARCA-PASTA-E21")],
            "uso": {"session": {"totalTokens": 10, "costUsdTicks": 5,
                                "modelUsage": {"MARCA-PROMPT-E21": {"inputTokens": 10}}}},
            "sinais": {"toolCallCount": 1, "workspace": "C:\\projetos\\MARCA-PASTA-E21"},
        }])
        pasta = raiz / "sessions" / GRUPO / id_sessao
        (pasta / "summary.json").write_text(json.dumps({
            "cwd": "C:\\projetos\\MARCA-PASTA-E21", "generated_title": "MARCA-TITULO-E21",
            "session_summary": "MARCA-TITULO-E21", "last_turn_summary": "MARCA-TITULO-E21"}), encoding="utf-8")
        (pasta / "chat_history.jsonl").write_text(json.dumps({"text": "MARCA-PROMPT-E21"}) + "\n", encoding="utf-8")
        (pasta / "prompt_history.jsonl").write_text(json.dumps({"text": "MARCA-PROMPT-E21"}) + "\n",
                                                    encoding="utf-8")
        saida = self.ler(raiz, Listar([proc(pid)]))
        itens = saida[mod.CHAVE]["sessoes"]
        self.assertEqual(len(itens), 1)
        self.assertEqual((itens[0]["estado"], itens[0]["ultima_ferramenta"]), ("trabalhando", "terminal"))
        texto = json.dumps(saida, ensure_ascii=False)
        for marca in MARCAS + (id_sessao, str(pid), "title", "summary"):
            with self.subTest(marca=marca):
                self.assertNotIn(marca, texto)
        self.assertEqual(set(itens[0]), {"chave", "ferramenta", "estado", "modelo", "contexto_pct", "tokens",
                                         "chamadas", "ultima_ferramenta", "atividade_epoch", "inicio_epoch"})
        for proibida in ("pid", "cwd", "session_id"):
            self.assertNotIn(proibida, itens[0])

    # 14
    def test_14_so_leitura_e_so_os_quatro_arquivos(self):
        id_sessao = ident(0xF14)
        raiz = montar(self.tmp / "grok", [{
            "id": id_sessao, "pid": 6141,
            "eventos": [ev("turn_started", 100, model_id="grok-4.7"), ev("turn_ended", 50)],
            "uso": {"session": {"totalTokens": 10}}, "sinais": {"toolCallCount": 1}}])
        pasta = raiz / "sessions" / GRUPO / id_sessao
        for nome in ("summary.json", "chat_history.jsonl", "prompt_history.jsonl", "prompt_context.json",
                     "system_prompt.txt", "updates.jsonl", "events.jsonl.lock"):
            (pasta / nome).write_text("MARCA-PROMPT-E21", encoding="utf-8")
        (pasta / "terminal").mkdir()
        (pasta / "terminal" / "1.log").write_text("MARCA-ARG-E21", encoding="utf-8")
        antes = retrato(self.tmp)
        abertos = []
        abrir, abrir_caminho = builtins.open, pathlib.Path.open

        def espia(arquivo, modo="r", *args, **kwargs):
            abertos.append((os.fspath(arquivo) if not isinstance(arquivo, int) else arquivo, modo))
            return abrir(arquivo, modo, *args, **kwargs)

        def espia_caminho(caminho, modo="r", *args, **kwargs):
            abertos.append((os.fspath(caminho), modo))
            return abrir_caminho(caminho, modo, *args, **kwargs)

        with patch("builtins.open", espia), patch.object(pathlib.Path, "open", espia_caminho):
            itens = self.sessoes(raiz, Listar([proc(6141)]))
        self.assertEqual(len(itens), 1)
        base = os.path.normcase(os.path.abspath(self.tmp))
        dentro = [(caminho, modo) for caminho, modo in abertos if isinstance(caminho, str) and
                  os.path.commonpath([os.path.normcase(os.path.abspath(caminho)), base]) == base]
        self.assertEqual({os.path.basename(caminho) for caminho, _ in dentro}, PERMITIDOS)
        for caminho, modo in dentro:
            with self.subTest(arquivo=os.path.basename(caminho), modo=modo):
                self.assertIn(os.path.basename(caminho), PERMITIDOS)
                self.assertIn("r", modo)
                self.assertTrue(set(modo) <= set("rbt"), modo)
        self.assertEqual(retrato(self.tmp), antes)


if __name__ == "__main__":
    unittest.main()
