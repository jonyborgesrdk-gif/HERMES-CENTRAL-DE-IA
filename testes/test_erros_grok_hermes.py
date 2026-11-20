"""E30: erros de Grok e Hermes no histórico de erros (lib/erros_registro.py e a fonte erros_agentes).

Só pastas temporárias, events.jsonl sintéticos e bancos SQLite criados no tempdir; todo caminho
de Grok, Hermes, Claude e Codex é passado explicitamente (ou Path.home/LOCALAPPDATA trocados por
pastas temporárias), então nenhum teste lê ~/.grok, ~/.claude, ~/.codex nem o state.db real.
Textos sintéticos de comando/saída carregam SEGREDO-CMD-X; pastas carregam SEGREDO-PASTA-Y.
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
from unittest.mock import patch

from lib import erros_registro as reg
from lib.fontes import erros_agentes as fonte

T = 1793433600.0
MTIME = T - 60
CMD = "SEGREDO-CMD-X"
PASTA = "SEGREDO-PASTA-Y"
COLUNAS = "id INTEGER PRIMARY KEY, session_id, role, tool_name, content, timestamp"


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def g(tipo, hora, **campos):
    """Uma linha do events.jsonl do Grok."""
    return dict({"ts": iso(hora), "type": tipo}, **campos)


def tool(nome, conteudo, hora, papel="tool"):
    """(role, tool_name, content, timestamp) de uma linha de messages do Hermes."""
    texto = conteudo if conteudo is None or isinstance(conteudo, str) else json.dumps(conteudo)
    return (papel, nome, texto, hora)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.dados = self.base / "dados"
        self.claude = self.base / "claude"
        self.codex = self.base / "codex"
        self.arq_claude = self.claude / "proj" / "sessao.jsonl"
        self.arq_codex = self.codex / "2026" / "11" / "15" / "rollout-a.jsonl"
        self.anexar(self.arq_claude, {"type": "system"})
        self.anexar(self.arq_codex, {"type": "session_meta"})
        self.grok_dir = self.base / ("grok-" + PASTA)
        self.grok = self.grok_dir / "sessions"
        self.events = self.grok / ("C--proj-" + PASTA) / "sessao-grok-1" / "events.jsonl"
        self.events.parent.mkdir(parents=True)
        self.pasta_hermes = self.base / ("hermes-" + PASTA)
        self.pasta_hermes.mkdir()
        self.banco = self.pasta_hermes / "state.db"

    # ------------------------------------------------------------ ajudantes

    def anexar(self, arq, *linhas, mtime=MTIME):
        """Acrescenta linhas e devolve a posição de início de cada uma."""
        arq.parent.mkdir(parents=True, exist_ok=True)
        pos = arq.stat().st_size if arq.exists() else 0
        inicios = []
        with open(arq, "ab") as f:
            for linha in linhas:
                bruto = linha if isinstance(linha, bytes) else (json.dumps(linha) + "\n").encode("utf-8")
                inicios.append(pos)
                pos += len(bruto)
                f.write(bruto)
        os.utime(arq, (mtime, mtime))
        return inicios

    def criar_banco(self, *linhas, banco=None, colunas=COLUNAS):
        with closing(sqlite3.connect(banco or self.banco)) as db:
            db.execute("CREATE TABLE messages (" + colunas + ")")
            db.commit()
        if linhas:
            self.inserir(*linhas, banco=banco)

    def inserir(self, *linhas, banco=None):
        with closing(sqlite3.connect(banco or self.banco)) as db:
            db.executemany("INSERT INTO messages (session_id, role, tool_name, content, timestamp) "
                           "VALUES (?, ?, ?, ?, ?)", [("sessao-" + CMD,) + linha for linha in linhas])
            db.commit()

    def passada(self, agora=T + 100, grok=True, hermes=True, banco=None):
        estado, erro = reg.varrer(self.dados, agora, self.claude, self.codex, self.grok if grok else None,
                                  (banco or self.banco) if hermes else None)
        self.assertIsNone(erro)
        self.assertIsNotNone(estado)
        return estado

    def rel(self):
        return self.events.relative_to(self.grok).as_posix()

    def resumo(self, estado, agora=T + 100):
        return reg.resumo_publico(estado, reg.carregar_registro(self.dados, True), agora)

    def erros_claude_codex(self, sufixo, hora=T + 5):
        self.anexar(self.arq_claude, {"type": "user", "uuid": "u-" + sufixo, "timestamp": iso(hora), "message": {
            "content": [{"type": "tool_result", "tool_use_id": "toolu_" + sufixo, "is_error": True,
                         "content": "Exit code 2\n" + CMD}]}})
        self.anexar(self.arq_codex, {"timestamp": iso(hora), "type": "event_msg", "payload": {
            "type": "item_completed", "thread_id": "thread-1", "completed_at_ms": int(hora * 1000),
            "item": {"type": "CommandExecution", "id": "call-" + sufixo, "status": "failed", "exit_code": 1,
                     "command": CMD}}})
        return reg.id_de("claude", "toolu_" + sufixo), reg.id_de("codex", "thread-1:call-" + sufixo)


class GrokTests(Base):
    # (a)
    def test_classifica_primeira_passada_e_incremental(self):
        self.criar_banco()
        self.anexar(self.events, g("tool_completed", T - 30, tool_name="read_file", tool_call_id="call-velho",
                                   outcome="error"))
        primeira = self.passada(T)
        self.assertEqual((primeira["ocorrencias"], primeira["sem_leitura"]), ({}, []))
        self.assertEqual(primeira["arquivos"][str(self.events)]["offset"], self.events.stat().st_size)
        offs = self.anexar(self.events, *[
            g("tool_completed", T + 1, tool_name="run_terminal_command", tool_call_id="call-ok", outcome="success",
              duration_ms=5),
            g("tool_completed", T + 2, tool_name="run_terminal_command", tool_call_id="call-1", outcome="error",
              args={"command": CMD}),
            g("permission_resolved", T + 3, tool_name="search_replace", decision="deny", wait_ms=3),
            g("permission_resolved", T + 4, tool_name="write", decision="cancelled"),
            g("permission_resolved", T + 5, tool_name="write", decision="allow"),
            g("turn_ended", T + 6, outcome="cancelled"),
            g("turn_ended", T + 7, outcome="completed"),
            g("tool_completed", T + 8, tool_name="use_tool", tool_call_id="call-2", outcome="error"),
            g("tool_completed", T + 9, tool_name="web_fetch", outcome="error"),
            b'nao e json "error" "deny"\n',
            g("tool_failed", T + 10, outcome="error"),
            g("permission_resolved", T + 11, tool_name="grep", decision="talvez"),
            g("tool_completed", T + 12, tool_name=["grep"], tool_call_id="x" * 201, outcome="error"),
            b'["error", "cancelled"]\n',
        ])
        estado = self.passada()
        rel = self.rel()
        esperado = {
            rel + "#call-1": ("comando", "outra", T + 2),
            rel + "@%d" % offs[2]: ("edit", "permissao_negada", T + 3),
            rel + "@%d" % offs[3]: ("write", "interrompido", T + 4),
            rel + "#call-2": ("outra", "outra", T + 8),
            rel + "@%d" % offs[8]: ("web", "outra", T + 9),
            rel + "@%d" % offs[12]: ("outra", "outra", T + 12),
        }
        obtido = {oc["chave"]: (oc["ferramenta"], oc["classe"], oc["hora"]) for oc in estado["ocorrencias"].values()}
        self.assertEqual(obtido, esperado)
        for ident, oc in estado["ocorrencias"].items():
            self.assertEqual((ident, oc["agente"], oc["codigo"], oc["arquivo"]),
                             (reg.id_de("grok", oc["chave"]), "grok", None, str(self.events)))
        resumo = self.resumo(estado)
        self.assertEqual(resumo["por_agente"], {"claude": 0, "codex": 0, "grok": 6, "hermes": 0})
        self.assertEqual((resumo["contagens"]["aberto"], resumo["contagens"]["contado"]), (4, 2))
        ids = set(estado["ocorrencias"])
        self.anexar(self.events, g("tool_completed", T + 150, tool_name="grep", tool_call_id="call-3",
                                   outcome="error"), mtime=MTIME + 1)
        segunda = self.passada(T + 200)
        novo = reg.id_de("grok", rel + "#call-3")
        self.assertEqual(set(segunda["ocorrencias"]), ids | {novo})
        self.assertEqual(segunda["ocorrencias"][novo]["ferramenta"], "grep")
        self.assertEqual(reg.carregar_ocorrencias(self.dados)["arquivos"][str(self.events)]["offset"],
                         self.events.stat().st_size)

    def test_arquivo_novo_le_do_inicio_com_corte_e_so_events_jsonl_duas_pastas_abaixo(self):
        self.criar_banco()
        self.passada(T)

        def erro(hora, ident):
            return g("tool_completed", hora, tool_call_id=ident, outcome="error")

        self.anexar(self.grok / "C--outra" / "sessao-grok-2" / "events.jsonl", erro(T - 30, "antes"),
                    erro(T + 20, "depois"))
        self.anexar(self.grok / "C--outra" / "events.jsonl", erro(T + 21, "raso"))
        self.anexar(self.grok / "C--outra" / "sessao-grok-3" / "eventos.jsonl", erro(T + 22, "nome"))
        self.anexar(self.grok / "C--outra" / "sessao-grok-4" / "sub" / "events.jsonl", erro(T + 23, "fundo"))
        estado = self.passada()
        self.assertEqual({oc["chave"] for oc in estado["ocorrencias"].values()},
                         {"C--outra/sessao-grok-2/events.jsonl#depois"})

    # M1: o turn_ended cancelled logo depois de deny/cancelled é eco da permissão, não uma segunda ocorrência
    def classes_depois(self, *linhas):
        self.criar_banco()
        self.anexar(self.events, g("tool_started", T - 30))
        self.passada(T)
        self.anexar(self.events, *linhas)
        return sorted(oc["classe"] for oc in self.passada()["ocorrencias"].values())

    def test_eco_deny_e_turn_ended_cancelled_conta_uma_vez(self):
        neutras = [g("assistant_message", T + 2), g("assistant_message", T + 3), g("assistant_message", T + 4)]
        self.assertEqual(self.classes_depois(
            g("permission_resolved", T + 1, tool_name="write", decision="deny"), *neutras,
            g("turn_ended", T + 5, outcome="cancelled")), ["permissao_negada"])

    def test_eco_permission_cancelled_e_turn_ended_cancelled_conta_uma_vez(self):
        self.assertEqual(self.classes_depois(
            g("permission_resolved", T + 1, tool_name="write", decision="cancelled"),
            g("turn_ended", T + 2, outcome="cancelled")), ["interrompido"])

    def test_turn_ended_cancelled_sem_permissao_antes_conta(self):
        self.assertEqual(self.classes_depois(g("turn_ended", T + 1, outcome="cancelled")), ["interrompido"])

    def test_turn_started_zera_a_permissao_pendente(self):
        self.assertEqual(self.classes_depois(
            g("permission_resolved", T + 1, tool_name="write", decision="deny"), g("turn_started", T + 2),
            g("turn_ended", T + 3, outcome="cancelled")), ["interrompido", "permissao_negada"])

    def test_eco_atravessa_passadas_pela_chave_permissao_pendente(self):
        self.criar_banco()
        self.anexar(self.events, g("tool_started", T - 30))
        self.passada(T)
        self.anexar(self.events, g("permission_resolved", T + 1, tool_name="write", decision="deny"),
                    g("assistant_message", T + 2))
        estado = self.passada()
        self.assertEqual([oc["classe"] for oc in estado["ocorrencias"].values()], ["permissao_negada"])
        self.assertIs(reg.carregar_ocorrencias(self.dados)["arquivos"][str(self.events)].get("permissao_pendente"), True)
        self.anexar(self.events, g("turn_ended", T + 3, outcome="cancelled"), mtime=MTIME + 1)
        estado = self.passada(T + 200)
        self.assertEqual([oc["classe"] for oc in estado["ocorrencias"].values()], ["permissao_negada"])
        self.assertNotIn("permissao_pendente", reg.carregar_ocorrencias(self.dados)["arquivos"][str(self.events)])


class HermesTests(Base):
    # (b)
    def test_classifica_e_primeira_passada_so_marca_max_id(self):
        self.criar_banco(tool("terminal", {"exit_code": 1, "output": CMD}, T - 30))
        primeira = self.passada(T)
        self.assertEqual((primeira["ocorrencias"], primeira["sem_leitura"]), ({}, []))
        self.assertEqual(primeira["arquivos"][str(self.banco)]["offset"], 1)
        casos = [
            ("terminal", {"exit_code": 0, "output": CMD}, None),
            ("process_manage", {"status": "dispatched"}, None),
            ("process_manage", {"status": "exited", "exit_code": 0}, None),
            ("terminal", {"exit_code": 1, "error": None, "output": CMD}, ("comando", "codigo_saida", 1)),
            ("execute_code", {"exit_code": 4294967295}, ("comando", "codigo_saida", 4294967295)),
            ("terminal", {"exit_code": -1}, ("comando", "codigo_saida", -1)),
            ("terminal", {"exit_code": 124, "output": CMD}, ("comando", "tempo_esgotado", 124)),
            ("browser_exec", {"status": "timeout"}, ("web", "tempo_esgotado", None)),
            ("terminal", {"status": "blocked", "error": CMD}, ("comando", "permissao_negada", None)),
            ("write_file", {"error": CMD + " falhou"}, ("write", "outra", None)),
            ("patch", {"success": False}, ("edit", "outra", None)),
            ("read_file", {"ok": False}, ("read", "outra", None)),
            ("terminal", {"exit_code": True, "success": True}, None),
            ("terminal", {"exit_code": True, "ok": False}, ("comando", "outra", None)),
            ("terminal", CMD + " nao e JSON", None),
            ("terminal", "[1, 2]", None),
            ("terminal", None, None),
            ("vision_analyze", {"error": "x"}, ("outra", "outra", None)),
            ("mcp_github_issue", {"success": False}, ("mcp", "outra", None)),
            ("search_files", {"error": ""}, None),
            ("search_files", {"error": []}, None),
            ("search_files", {"error": {}}, None),
            ("search_files", {"error": False}, None),
            ("delegate_task", {"status": "error"}, ("agent", "outra", None)),
            (None, {"exit_code": 2}, ("outra", "codigo_saida", 2)),
            ("terminal", {"exit_code": 99999999999}, ("comando", "codigo_saida", None)),
        ]
        linhas = [tool(nome, conteudo, T + i + 1) for i, (nome, conteudo, _e) in enumerate(casos)]
        linhas.append(tool("terminal", {"exit_code": 1}, T + 90, papel="assistant"))
        self.inserir(*linhas)
        estado = self.passada()
        esperado = {str(i + 2): e + (T + i + 1,) for i, (_n, _c, e) in enumerate(casos) if e is not None}
        obtido = {oc["chave"]: (oc["ferramenta"], oc["classe"], oc["codigo"], oc["hora"])
                  for oc in estado["ocorrencias"].values()}
        self.assertEqual(obtido, esperado)
        for ident, oc in estado["ocorrencias"].items():
            self.assertEqual((ident, oc["agente"], oc["arquivo"], oc["offset"]),
                             (reg.id_de("hermes", oc["chave"]), "hermes", str(self.banco), int(oc["chave"])))
        self.assertEqual(estado["arquivos"][str(self.banco)]["offset"], len(casos) + 1)
        resumo = self.resumo(estado)
        self.assertEqual(resumo["contagens"]["contado"], 1)
        self.assertEqual(resumo["por_agente"]["hermes"], len(esperado))

    def test_banco_recriado_volta_ao_inicio(self):
        self.criar_banco(*[tool("terminal", {"exit_code": 0}, T - 30) for _ in range(5)])
        self.assertEqual(self.passada(T)["arquivos"][str(self.banco)]["offset"], 5)
        self.banco.unlink()
        self.criar_banco(tool("terminal", {"exit_code": 3}, T + 50))
        estado = self.passada()
        self.assertEqual({oc["chave"]: oc["codigo"] for oc in estado["ocorrencias"].values()}, {"1": 3})

    # N2: o corte por criado_em vale no SQL; linhas antigas não gastam o orçamento de bytes
    def test_linhas_antigas_nao_gastam_o_orcamento(self):
        self.criar_banco()
        self.passada(T)                        # Hermes iniciado com cursor no MAX(id) = 0
        antigo = json.dumps({"exit_code": 1, "output": "x" * 480})
        self.inserir(*[tool("terminal", antigo, T - 100) for _ in range(5)],
                     tool("terminal", {"exit_code": 2}, T + 10))
        with patch.object(reg, "ORCAMENTO_BYTES", 1000):
            estado = self.passada(T + 20)
        self.assertEqual([(oc["chave"], oc["codigo"]) for oc in estado["ocorrencias"].values()], [("6", 2)])
        self.assertEqual(estado["arquivos"][str(self.banco)]["offset"], 6)

    # N3: tamanho em bytes e linha gigante
    def test_content_gigante_nao_e_decodificado_e_cursor_avanca(self):
        self.criar_banco()
        self.passada(T)
        self.inserir(tool("terminal", {"exit_code": 1, "output": "y" * 200}, T + 5))
        with patch.object(reg, "MAX_LINHA", 64):
            estado = self.passada()
        self.assertEqual(estado["ocorrencias"], {})
        self.assertEqual(estado["arquivos"][str(self.banco)]["offset"], 1)

    def test_orcamento_do_hermes_conta_bytes_e_nao_caracteres(self):
        self.criar_banco()
        self.passada(T)
        longo = json.dumps({"error": "é" * 60}, ensure_ascii=False)
        self.assertTrue(len(longo) < 100 < len(longo.encode("utf-8")))
        self.inserir(tool("terminal", longo, T + 5), tool("terminal", {"exit_code": 2}, T + 6))
        with patch.object(reg, "ORCAMENTO_BYTES", 100):
            estado = self.passada()
        self.assertEqual([oc["chave"] for oc in estado["ocorrencias"].values()], ["1"])
        self.assertEqual(estado["arquivos"][str(self.banco)]["offset"], 1)

    # (e)
    def test_banco_intacto(self):
        self.criar_banco(tool("terminal", {"exit_code": 1}, T - 30))
        self.passada(T)
        self.inserir(tool("terminal", {"exit_code": 2}, T + 5), tool("patch", {"success": False}, T + 6))

        def retrato():
            saida = {}
            for nome in sorted(os.listdir(self.pasta_hermes)):
                p = self.pasta_hermes / nome
                info = p.stat()
                saida[nome] = (info.st_size, info.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            return saida

        antes = retrato()
        self.assertEqual(list(antes), ["state.db"])
        self.assertEqual(len(self.passada()["ocorrencias"]), 2)
        self.passada(T + 110)
        self.assertEqual(retrato(), antes)


class RegistroTests(Base):
    # (c)
    def test_ids_estaveis_e_fluxo_de_correcao(self):
        self.criar_banco()
        self.passada(T)
        self.anexar(self.events, g("tool_completed", T + 2, tool_name="read_file", tool_call_id="call-r",
                                   outcome="error"),
                    g("permission_resolved", T + 3, tool_name="write", decision="deny"))
        self.inserir(tool("terminal", {"exit_code": 1}, T + 4), tool("terminal", {"status": "blocked"}, T + 5))
        primeira = self.passada()
        segunda = self.passada(T + 110)
        self.assertEqual(set(primeira["ocorrencias"]), set(segunda["ocorrencias"]))
        self.assertEqual(len(segunda["ocorrencias"]), 4)
        id_grok = reg.id_de("grok", self.rel() + "#call-r")
        id_hermes = reg.id_de("hermes", "1")
        self.assertIn(id_grok, segunda["ocorrencias"])
        self.assertIn(id_hermes, segunda["ocorrencias"])
        for ident in (id_grok, id_hermes):
            self.assertIsNone(reg.ler_texto(segunda["ocorrencias"][ident]))
        contados = [i for i, oc in segunda["ocorrencias"].items() if oc["classe"] == "permissao_negada"]
        self.assertEqual(len(contados), 2)
        for ident in contados:
            with self.assertRaises(reg.ErroRegistro):
                reg.registrar_corrigido(self.dados, ident, None, T + 150)
        with self.assertRaises(reg.ErroRegistro):
            reg.registrar_verificado(self.dados, id_hermes, "teste", "py -m unittest", 0,
                                     str(self.base / "nao-existe.txt"), T + 160)
        reg.registrar_corrigido(self.dados, id_grok, "nota", T + 150)
        self.passada(T + 155)
        estados = {i["id"]: i["estado"] for i in reg.listar(self.dados, T + 156)}
        self.assertEqual((estados[id_grok], estados[id_hermes]), ("corrigido", "aberto"))
        saida = self.base / "saida.txt"
        saida.write_bytes(b"OK\n")
        reg.registrar_verificado(self.dados, id_hermes, "teste", "py -m unittest", 0, str(saida), T + 160)
        reg.registrar_verificado(self.dados, id_grok, "comando", "py -m x", 0, str(saida), T + 170)
        terceira = self.passada(T + 200)
        self.assertEqual(set(terceira["ocorrencias"]), set(primeira["ocorrencias"]))
        estados = {i["id"]: i["estado"] for i in reg.listar(self.dados, T + 210)}
        self.assertEqual((estados[id_grok], estados[id_hermes]), ("verificado", "verificado"))
        itens = {i["id"]: i for i in self.resumo(terceira, T + 210)["itens"]}
        self.assertEqual(itens[id_hermes]["evidencia"], {"tipo": "teste", "codigo": 0})


class SemLeituraTests(Base):
    # (d)
    def test_hermes_sem_leitura_nao_derruba_claude_codex(self):
        def ausente(banco):
            pass

        def corrompido(banco):
            banco.write_bytes(b"isto nao e um banco SQLite " * 64)

        def sem_coluna(banco):
            self.criar_banco(banco=banco, colunas="id INTEGER PRIMARY KEY, role, tool_name, timestamp")

        def sem_tabela(banco):
            with closing(sqlite3.connect(banco)) as db:
                db.execute("CREATE TABLE outra (x)")
                db.commit()

        def travado(banco):
            # Com -wal e -shm a abertura é mode=ro (com trava); a escritora em locking_mode=EXCLUSIVE
            # segura a trava exclusiva do arquivo depois do BEGIN EXCLUSIVE.
            self.criar_banco(tool("terminal", {"exit_code": 1}, T - 30), banco=banco)
            escritora = sqlite3.connect(banco, isolation_level=None)
            self.addCleanup(escritora.close)
            escritora.execute("PRAGMA journal_mode=WAL")
            escritora.execute("INSERT INTO messages (role) VALUES ('user')")
            escritora.execute("PRAGMA locking_mode=EXCLUSIVE")
            escritora.execute("BEGIN EXCLUSIVE")
            escritora.execute("INSERT INTO messages (role) VALUES ('user')")
            self.assertTrue(os.path.isfile(str(banco) + "-wal") and os.path.isfile(str(banco) + "-shm"))

        for nome, preparar in (("ausente", ausente), ("corrompido", corrompido), ("sem_coluna", sem_coluna),
                               ("sem_tabela", sem_tabela), ("travado", travado)):
            with self.subTest(nome):
                banco = self.pasta_hermes / (nome + ".db")
                preparar(banco)
                self.dados = self.base / ("dados-" + nome)
                primeira = self.passada(T, banco=banco)
                self.assertEqual(primeira["sem_leitura"], ["hermes"])
                self.assertNotIn(str(banco), primeira["arquivos"])
                id_claude, id_codex = self.erros_claude_codex(nome)
                estado = self.passada(banco=banco)
                self.assertEqual(estado["sem_leitura"], ["hermes"])
                self.assertEqual(set(estado["ocorrencias"]), {id_claude, id_codex})
                self.assertEqual(self.resumo(estado)["sem_leitura"], ["hermes"])
                self.assertNotIn("sem_leitura", json.loads((self.dados / reg.ARQ_OCORRENCIAS).read_text("utf-8")))

    def test_grok_sem_leitura_e_ordem(self):
        self.criar_banco()
        arquivo = self.base / "um-arquivo"
        arquivo.write_text("x", encoding="utf-8")
        for nome, raiz in (("inexistente", self.base / "nao-existe"), ("arquivo", arquivo)):
            with self.subTest(nome):
                self.grok = raiz
                self.dados = self.base / ("dados-" + nome)
                self.assertEqual(self.passada(T)["sem_leitura"], ["grok"])
                id_claude, id_codex = self.erros_claude_codex(nome)
                estado = self.passada()
                self.assertEqual(estado["sem_leitura"], ["grok"])
                self.assertEqual(set(estado["ocorrencias"]), {id_claude, id_codex})
        self.assertEqual(self.passada(T + 120, banco=self.pasta_hermes / "nao-existe.db")["sem_leitura"],
                         ["grok", "hermes"])
        self.assertEqual(self.passada(T + 130, grok=False, hermes=False)["sem_leitura"], [])

    # N5: arquivo não vazio sem nenhum tipo de evento conhecido = formato desconhecido
    def test_grok_formato_desconhecido_e_sem_leitura(self):
        self.criar_banco()
        self.anexar(self.events, g("tipo_novo", T - 30))
        self.assertEqual(self.passada(T)["sem_leitura"], ["grok"])
        self.anexar(self.events, g("outro_tipo", T + 1), mtime=MTIME + 1)
        self.assertEqual(self.passada()["sem_leitura"], ["grok"])
        self.anexar(self.grok / "C--outra" / "sessao-grok-2" / "events.jsonl", g("tool_started", T + 2))
        self.assertEqual(self.passada(T + 110)["sem_leitura"], [])

    def test_grok_so_arquivo_vazio_nao_e_sem_leitura(self):
        self.criar_banco()
        self.anexar(self.events)
        self.assertEqual(self.events.stat().st_size, 0)
        self.assertEqual(self.passada(T)["sem_leitura"], [])
        self.assertEqual(self.passada()["sem_leitura"], [])

    def test_resumo_sem_leitura_so_agentes_conhecidos(self):
        for bruto, esperado in ((None, []), ("grok", []), (["hermes", "grok", "hermes", "cursor", 7], ["grok", "hermes"])):
            estado = {"versao": 1, "criado_em": T, "arquivos": {}, "ocorrencias": {}}
            if bruto is not None:
                estado["sem_leitura"] = bruto
            self.assertEqual(reg.resumo_publico(estado, {}, T)["sem_leitura"], esperado)


class FonteTests(Base):
    def ctx(self, agora):
        return {"dir": str(self.dados), "claude_projects_dir": self.claude, "codex_sessions_dir": self.codex,
                "grok_dir": self.grok_dir, "hermes_db": self.banco, "agora": agora}

    # (f)
    def test_privacidade_resumo_e_fonte(self):
        self.criar_banco()
        self.assertEqual(fonte.ler(self.ctx(T))["erros_agentes"]["sem_leitura"], [])
        self.anexar(self.events, g("tool_completed", T + 2, tool_name="run_terminal_command",
                                   tool_call_id="call-" + CMD, outcome="error", args={"command": CMD}, output=CMD),
                    g("permission_resolved", T + 3, tool_name="write", decision="deny", args={"path": "C:/" + PASTA}))
        self.inserir(tool("terminal", {"exit_code": 1, "error": CMD, "output": CMD + " C:/" + PASTA}, T + 4),
                     tool("write_file", {"status": "error", "error": "C:/" + PASTA}, T + 5))
        saida = fonte.ler(self.ctx(T + 100))
        resumo = saida["erros_agentes"]
        self.assertEqual(resumo["por_agente"], {"claude": 0, "codex": 0, "grok": 2, "hermes": 2})
        self.assertEqual(len(resumo["itens"]), 3)
        texto = json.dumps(saida) + json.dumps(reg.resumo_publico(reg.carregar_ocorrencias(self.dados), {}, T + 100))
        for proibido in (CMD, PASTA, "call-", "sessao-grok-1", self.rel(), str(self.base), self.base.as_posix(),
                         self.base.name, json.dumps(str(self.base))[1:-1], str(self.banco), "state.db"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, texto)

    def test_sem_caminhos_usa_home_e_localappdata(self):
        casa = self.base / "casa"
        local = self.base / "local"
        casa.mkdir()
        with patch.object(Path, "home", return_value=casa), patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
            primeira = fonte.ler({"dir": str(self.dados), "agora": T})
            (casa / ".grok" / "sessions").mkdir(parents=True)
            (local / "hermes").mkdir(parents=True)
            self.criar_banco(banco=local / "hermes" / "state.db")
            segunda = fonte.ler({"dir": str(self.dados), "agora": T + 10})
        self.assertEqual(primeira["erros_agentes"]["sem_leitura"], ["grok", "hermes"])
        self.assertEqual(segunda["erros_agentes"]["sem_leitura"], [])


class IniciadosTests(Base):
    """E30-MICRO: A1 (sonda do formato Grok) e agentes_iniciados (Grok/Hermes contam só da publicação)."""

    def estado_e20(self):
        """Estado E20 válido (sem agentes_iniciados) com criado_em anterior aos erros históricos."""
        self.passada(T - 1000, grok=False, hermes=False)
        caminho = self.dados / reg.ARQ_OCORRENCIAS
        obj = json.loads(caminho.read_text("utf-8"))
        self.assertIn("agentes_iniciados", obj)
        del obj["agentes_iniciados"]
        caminho.write_bytes(json.dumps(obj).encode("utf-8"))

    def maximo_hermes(self):
        with closing(sqlite3.connect(self.banco)) as db:
            return db.execute("SELECT MAX(id) FROM messages").fetchone()[0]

    def gravado(self):
        return json.loads((self.dados / reg.ARQ_OCORRENCIAS).read_text("utf-8"))

    # A1
    def test_grok_so_com_eventos_mcp_nao_e_sem_leitura(self):
        self.criar_banco()
        self.anexar(self.events, *[g(t, T - 30) for t in (
            "mcp_config_resolved", "mcp_server_starting", "mcp_server_connected", "mcp_init_completed")])
        for agora in (T, T + 100):
            estado = self.passada(agora)
            self.assertNotIn("grok", estado["sem_leitura"])
            self.assertEqual([o for o in estado["ocorrencias"].values() if o["agente"] == "grok"], [])

    def test_grok_tipos_de_sessao_tambem_sao_conhecidos(self):
        self.criar_banco()
        for tipo in ("turn_started", "permission_requested", "phase_changed", "loop_started", "first_token"):
            with self.subTest(tipo):
                self.dados = self.base / ("dados-" + tipo)
                self.events.write_bytes(b"")
                self.anexar(self.events, g(tipo, T - 30))
                self.assertNotIn("grok", self.passada(T)["sem_leitura"])

    def test_primeira_passada_com_estado_e20_so_marca_grok_e_hermes(self):
        self.estado_e20()
        self.anexar(self.events, g("tool_completed", T - 500, tool_name="run_terminal_command",
                                   tool_call_id="call-hist", outcome="error"))
        self.criar_banco(tool("terminal", {"exit_code": 1}, T - 500))
        estado = self.passada()
        self.assertEqual([o for o in estado["ocorrencias"].values() if o["agente"] in ("grok", "hermes")], [])
        self.assertEqual(estado["sem_leitura"], [])
        tam = self.events.stat().st_size
        self.assertEqual(estado["arquivos"][str(self.events)]["offset"], tam)
        self.assertEqual(estado["arquivos"][str(self.banco)]["offset"], self.maximo_hermes())
        self.assertEqual(estado["agentes_iniciados"], ["claude", "codex", "grok", "hermes"])
        self.assertEqual(self.gravado()["agentes_iniciados"], ["claude", "codex", "grok", "hermes"])
        self.assertNotIn("agentes_iniciados", self.resumo(estado))
        self.assertNotIn("agentes_iniciados", json.dumps(self.resumo(estado)))

        # depois da inicialização: erros novos contam
        self.anexar(self.events, g("tool_completed", T + 1, tool_name="run_terminal_command",
                                   tool_call_id="call-novo", outcome="error"))
        self.inserir(tool("terminal", {"exit_code": 1}, T + 2))
        estado = self.passada()
        por_agente = sorted(o["agente"] for o in estado["ocorrencias"].values() if o["agente"] in ("grok", "hermes"))
        self.assertEqual(por_agente, ["grok", "hermes"])

        # arquivo Grok novo, criado depois, é lido do byte 0
        novo = self.grok / "C--outra" / "sessao-grok-2" / "events.jsonl"
        self.anexar(novo, g("tool_completed", T + 3, tool_name="run_terminal_command", tool_call_id="call-n2",
                            outcome="error"))
        estado = self.passada()
        self.assertEqual(len([o for o in estado["ocorrencias"].values() if o["agente"] == "grok"]), 2)

    def test_inicia_todos_arquivos_grok_mesmo_com_orcamento_esgotado(self):
        self.estado_e20()
        caminhos = (self.events, self.grok / "C--outra" / "sessao-grok-2" / "events.jsonl")
        for caminho in caminhos:
            self.anexar(caminho, g("tool_completed", T - 500, tool_call_id="call-hist", outcome="error"))
        self.anexar(self.arq_claude, {"type": "system"}, mtime=T)
        with patch.object(reg, "ORCAMENTO_BYTES", 1):
            primeira = self.passada(T, hermes=False)
        self.assertIn("grok", primeira["agentes_iniciados"])
        self.assertEqual(primeira["ocorrencias"], {})
        self.assertEqual(self.passada(T + 10, hermes=False)["ocorrencias"], {})
        for caminho in caminhos:
            self.assertEqual(primeira["arquivos"][str(caminho)]["offset"], caminho.stat().st_size)
            self.assertEqual(self.gravado()["arquivos"][str(caminho)]["offset"], caminho.stat().st_size)
            self.anexar(caminho, g("tool_completed", T + 20, tool_call_id="call-novo", outcome="error"),
                        mtime=T + 20)
        estado = self.passada(T + 30, hermes=False)
        self.assertEqual({(oc["agente"], oc["chave"]) for oc in estado["ocorrencias"].values()},
                         {("grok", caminho.relative_to(self.grok).as_posix() + "#call-novo") for caminho in caminhos})

    def test_hermes_ilegivel_nao_inicia_e_tenta_de_novo(self):
        self.estado_e20()
        self.criar_banco(tool("terminal", {"exit_code": 1}, T - 500))
        ausente = self.pasta_hermes / "ausente.db"
        estado = self.passada(banco=ausente)
        self.assertEqual(estado["sem_leitura"], ["hermes"])
        self.assertNotIn("hermes", estado["agentes_iniciados"])
        estado = self.passada()
        self.assertEqual(estado["sem_leitura"], [])
        self.assertIn("hermes", estado["agentes_iniciados"])
        self.assertEqual([o for o in estado["ocorrencias"].values() if o["agente"] == "hermes"], [])

    def test_fonte_desconfigurada_sai_da_lista(self):
        self.criar_banco()
        self.anexar(self.events, g("turn_started", T - 30))
        self.assertEqual(self.passada(T)["agentes_iniciados"], ["claude", "codex", "grok", "hermes"])
        self.assertEqual(self.passada(grok=False, hermes=False)["agentes_iniciados"], ["claude", "codex"])

    def test_estado_antigo_e_chave_invalida_carregam(self):
        self.passada(T - 1000, grok=False, hermes=False)
        caminho = self.dados / reg.ARQ_OCORRENCIAS
        base = json.loads(caminho.read_text("utf-8"))
        del base["agentes_iniciados"]
        casos = (("ausente", None, ["claude", "codex"]), ("string", "x", ["claude", "codex"]),
                 ("lixo", [1, "zzz"], ["claude", "codex"]), ("valida", ["hermes", "grok", "x"],
                                                           ["claude", "codex", "grok", "hermes"]))
        for nome, valor, esperado in casos:
            with self.subTest(nome):
                obj = dict(base)
                if valor is not None:
                    obj["agentes_iniciados"] = valor
                caminho.write_bytes(json.dumps(obj).encode("utf-8"))
                self.assertEqual(reg.carregar_ocorrencias(self.dados)["agentes_iniciados"], esperado)


if __name__ == "__main__":
    unittest.main()
