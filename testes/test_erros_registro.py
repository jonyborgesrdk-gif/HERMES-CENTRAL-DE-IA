"""E20: lib/erros_registro.py (captura, classificação, estado, resumo público, texto e registro).

Só pastas temporárias e transcripts sintéticos; nenhum teste lê ~/.claude nem ~/.codex.
Os textos sintéticos de erro, de comando e de pasta carregam SEGREDO-ERRO, SEGREDO-COMANDO
e SEGREDO-PASTA; os nomes dos arquivos e das pastas temporárias não carregam marca.
"""
import builtins
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from lib import erros_registro as reg

T = 1793433600.0
MTIME = T - 60
MARCAS = ("SEGREDO-ERRO", "SEGREDO-COMANDO", "SEGREDO-PASTA")
RE_ID = re.compile(r"^[0-9a-f]{10}$")


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def jl(obj, compacto=True):
    separadores = (",", ":") if compacto else None
    return (json.dumps(obj, ensure_ascii=False, separators=separadores) + "\n").encode("utf-8")


def uso(tid, nome="Bash", comando="echo SEGREDO-COMANDO", hora=T + 4):
    return {"type": "assistant", "uuid": "a-" + tid, "timestamp": iso(hora), "sessionId": "sessao-1",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": nome, "input": {"command": comando}}]}}


def resultado(tid, texto="Exit code 2\nSEGREDO-ERRO", erro=True):
    return {"type": "tool_result", "tool_use_id": tid, "is_error": erro, "content": texto}


def falha(tid, texto="Exit code 2\nSEGREDO-ERRO", hora=T + 5, **extra):
    linha = {"type": "user", "uuid": "u-" + tid, "timestamp": iso(hora), "cwd": "C:/proj/SEGREDO-PASTA",
             "sessionId": "sessao-1", "message": {"role": "user", "content": [resultado(tid, texto)]}}
    linha.update(extra)
    return linha


def item_codex(iid, tipo="CommandExecution", status="failed", **campos):
    item = {"type": tipo, "id": iid, "status": status, "exit_code": 1, "command": "bash -lc 'echo SEGREDO-COMANDO'",
            "cwd": "/home/x/SEGREDO-PASTA", "aggregated_output": "SEGREDO-ERRO: falhou"}
    item.update(campos)
    return item


def evento(item, hora=T + 7, thread="thread-1"):
    return {"timestamp": iso(hora - 1), "type": "event_msg",
            "payload": {"type": "item_completed", "thread_id": thread, "turn_id": "turn-1", "item": item,
                        "started_at_ms": int((hora - 2) * 1000), "completed_at_ms": int(hora * 1000)}}


def aborto(turno="turn-9", motivo="interrupted", hora=T + 8):
    return {"timestamp": iso(hora), "type": "event_msg",
            "payload": {"type": "turn_aborted", "turn_id": turno, "reason": motivo}}


def retrato(raiz):
    saida = {}
    for p in sorted(Path(raiz).rglob("*")):
        if p.is_file():
            info = p.stat()
            saida[str(p)] = (info.st_size, info.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            saida[str(p)] = "pasta"
    return saida


class ErrosRegistroTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.dados = self.base / "dados"
        self.claude = self.base / "claude"
        self.codex = self.base / "codex"
        self.claude.mkdir()
        self.codex.mkdir()
        self.arq = self.claude / "proj" / "sessao.jsonl"
        self.rollout = self.codex / "2026" / "11" / "15" / "rollout-a.jsonl"
        self.zst = self.rollout.with_name(self.rollout.name + ".zst")

    # ------------------------------------------------------------ ajudantes

    def escrever(self, caminho, *linhas, mtime=MTIME, modo="ab"):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, modo) as f:
            for linha in linhas:
                f.write(linha if isinstance(linha, bytes) else jl(linha))
        os.utime(caminho, (mtime, mtime))
        return caminho

    def passada(self, agora=T + 100):
        estado, erro = reg.varrer(self.dados, agora, self.claude, self.codex)
        self.assertIsNone(erro)
        self.assertIsNotNone(estado)
        return estado

    def marcar(self):
        return self.passada(T)

    def disco(self):
        return json.loads((self.dados / reg.ARQ_OCORRENCIAS).read_text(encoding="utf-8"))

    def por_chave(self, estado):
        return {oc["chave"]: oc for oc in estado["ocorrencias"].values()}

    def registro_disco(self):
        return json.loads((self.dados / reg.ARQ_REGISTRO).read_text(encoding="utf-8"))

    def tres_abertos(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, uso("toolu_A"), falha("toolu_A"), uso("toolu_B"), falha("toolu_B"),
                      uso("toolu_C"), falha("toolu_C"))
        self.passada()
        return [reg.id_de("claude", "toolu_" + x) for x in "ABC"]

    # ------------------------------------------------------------ §10, itens 1 a 40

    def test_01_primeira_passada_so_marca_posicao(self):
        self.escrever(self.arq, uso("toolu_A"), falha("toolu_A"))
        self.escrever(self.rollout, evento(item_codex("call-1")))
        estado = self.marcar()
        self.assertEqual(estado["ocorrencias"], {})
        disco = self.disco()
        self.assertEqual(disco["criado_em"], T)
        self.assertEqual(disco["ocorrencias"], {})
        self.assertEqual(set(disco["arquivos"]), {str(self.arq), str(self.rollout)})
        for caminho, entrada in disco["arquivos"].items():
            tam = os.path.getsize(caminho)
            self.assertEqual(entrada["offset"], tam)
            self.assertEqual(entrada["tam"], tam)
        self.assertEqual(self.passada()["ocorrencias"], {})

    def test_02_erro_bash_codigo_saida_capturado(self):
        self.escrever(self.arq, {"type": "system", "subtype": "inicio"})
        self.marcar()
        self.escrever(self.arq, uso("toolu_A"))
        inicio = os.path.getsize(self.arq)
        self.escrever(self.arq, falha("toolu_A", hora=T + 5))
        estado = self.passada()
        self.assertEqual(len(estado["ocorrencias"]), 1)
        (ident, oc), = estado["ocorrencias"].items()
        self.assertEqual(oc, {"id": reg.id_de("claude", "toolu_A"), "agente": "claude", "ferramenta": "bash",
                              "classe": "codigo_saida", "codigo": 2, "hora": T + 5, "arquivo": str(self.arq),
                              "offset": inicio, "chave": "toolu_A"})
        self.assertEqual(ident, oc["id"])
        self.assertEqual(self.disco()["ocorrencias"], estado["ocorrencias"])

    def test_03_classificar_claude(self):
        user = {"type": "user"}
        casos = [
            ("toolDenialKind", dict(user, toolDenialKind="permission-rule"), "Exit code 1", ("permissao_negada", None)),
            ("reject", dict(user, permissionDecision={"decision": "reject", "source": "hook"}), "Exit code 1",
             ("permissao_negada", None)),
            ("permission to use", user, "Permission to use Bash has been denied. SEGREDO-ERRO", ("permissao_negada", None)),
            ("nao quer seguir", user, "The user doesn't want to proceed with this tool use.", ("permissao_negada", None)),
            ("pediu permissao", user, "Claude requested permissions to write to C:/x, but you haven't granted it yet.",
             ("permissao_negada", None)),
            ("interrompido", user, "[Request interrupted by user for tool use]", ("interrompido", None)),
            ("edicao", user, "<tool_use_error>String to replace not found in file.\nString: SEGREDO-ERRO</tool_use_error>",
             ("edicao", None)),
            ("nao lido", user, "<tool_use_error>File has not been read yet. Read it first before writing to it."
             "</tool_use_error>", ("arquivo", None)),
            ("nao existe", user, "File does not exist. Note: your current working directory is C:/SEGREDO-PASTA.",
             ("arquivo", None)),
            ("tempo", user, "Command timed out after 2m 0s", ("tempo_esgotado", None)),
            ("validacao", user, "<tool_use_error>InputValidationError: SEGREDO-ERRO</tool_use_error>", ("validacao", None)),
            ("livre", user, "algo deu errado: SEGREDO-ERRO", ("outra", None)),
            ("127", user, "Exit code 127\nbash: x: command not found", ("codigo_saida", 127)),
            ("accept", dict(user, permissionDecision={"decision": "accept"}), "Exit code 1", ("codigo_saida", 1)),
            ("lista", user, [{"type": "text", "text": "Exit code 3"}, {"type": "text", "text": "SEGREDO-ERRO"}],
             ("codigo_saida", 3)),
        ]
        for nome, linha, conteudo, esperado in casos:
            with self.subTest(nome):
                self.assertEqual(reg.classificar_claude(linha, resultado("toolu_C", conteudo)), esperado)

    def test_04_ferramenta_claude(self):
        tabela = {"Bash": "bash", "PowerShell": "powershell", "Read": "read", "Edit": "edit", "NotebookEdit": "edit",
                  "Write": "write", "Grep": "grep", "Glob": "glob", "Agent": "agent", "Task": "agent",
                  "WebFetch": "web", "WebSearch": "web", "mcp__a__b": "mcp", "Desconhecida": "outra", None: "outra",
                  7: "outra"}
        for nome, esperado in tabela.items():
            with self.subTest(nome=nome):
                self.assertEqual(reg.ferramenta_claude(nome), esperado)
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, uso("toolu_LONGE"), {"type": "system", "texto": "x" * 1000}, falha("toolu_LONGE"),
                      uso("toolu_PERTO", nome="Grep"), falha("toolu_PERTO"))
        janela = len(jl(uso("toolu_PERTO", nome="Grep"))) + 10
        with patch.object(reg, "JANELA_TOOL_USE", janela):
            por = self.por_chave(self.passada())
        self.assertEqual(por["toolu_LONGE"]["ferramenta"], "outra")
        self.assertEqual(por["toolu_PERTO"]["ferramenta"], "grep")

    def test_05_api_request_blob_e_assistant_nao_geram_ocorrencia(self):
        blob = {"type": "api-request-blob", "timestamp": iso(T + 5),
                "message": {"role": "user", "content": [resultado("toolu_BLOB")]}}
        assistente = {"type": "assistant", "uuid": "a-x", "timestamp": iso(T + 5),
                      "message": {"content": [resultado("toolu_ASSIS")]}}
        self.assertEqual(reg.ocorrencias_claude(blob), [])
        self.assertEqual(reg.ocorrencias_claude(assistente), [])
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, blob, assistente, falha("toolu_CTRL"))
        self.assertEqual(set(self.por_chave(self.passada())), {"toolu_CTRL"})

    def test_06_is_error_e_linhas_que_nao_decodificam(self):
        falso = falha("toolu_F")
        falso["message"]["content"][0]["is_error"] = False
        ausente = falha("toolu_AUS")
        del ausente["message"]["content"][0]["is_error"]
        texto = falha("toolu_TXT")
        texto["message"]["content"][0]["is_error"] = "true"
        for linha in (falso, ausente, texto):
            self.assertEqual(reg.ocorrencias_claude(linha), [])
        espacado = jl(falha("toolu_ESP"), compacto=False)
        self.assertIn(b'"is_error": true', espacado)
        cortada = (b'{"type":"user","uuid":"u-corte","message":{"content":[{"type":"tool_result",'
                   b'"tool_use_id":"toolu_CORTE","is_error":true\n')
        funda = b'{"type":"outro","marca":"[Request interrupted","x":' + b"[" * 200000 + b"]" * 200000 + b"}\n"
        with self.assertRaises(RecursionError):
            json.loads(funda)
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, falso, ausente, texto, espacado, cortada, funda, falha("toolu_OK"))
        estado = self.passada()
        self.assertEqual(set(self.por_chave(estado)), {"toolu_ESP", "toolu_OK"})
        self.assertEqual(self.disco()["arquivos"][str(self.arq)]["offset"], os.path.getsize(self.arq))

    def test_07_erro_de_api(self):
        api = {"type": "assistant", "uuid": "api-1", "timestamp": iso(T + 5), "isApiErrorMessage": True,
               "error": "rate_limit", "apiErrorStatus": 429,
               "message": {"content": [{"type": "text", "text": "API Error: SEGREDO-ERRO"}]}}
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, api)
        oc = self.por_chave(self.passada())["api:api-1"]
        self.assertEqual((oc["classe"], oc["ferramenta"], oc["codigo"]), ("api", "nenhuma", 429))
        for status in (True, "429", 600, 99, 429.0):
            with self.subTest(status=status):
                (o,) = reg.ocorrencias_claude(dict(api, apiErrorStatus=status))
                self.assertIsNone(o["codigo"])
        sem_uuid = dict(api)
        del sem_uuid["uuid"]
        self.assertEqual(reg.ocorrencias_claude(sem_uuid), [])
        self.assertEqual(reg.ocorrencias_claude(dict(api, uuid="")), [])

    def test_08_interrupcao_em_item_text(self):
        inter = {"type": "user", "uuid": "int-1", "timestamp": iso(T + 5),
                 "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, inter)
        oc = self.por_chave(self.passada())["int:int-1"]
        self.assertEqual((oc["classe"], oc["ferramenta"], oc["codigo"]), ("interrompido", "nenhuma", None))
        sem_uuid = dict(inter)
        del sem_uuid["uuid"]
        self.assertEqual(reg.ocorrencias_claude(sem_uuid), [])
        junto = falha("toolu_J")
        junto["message"]["content"].append({"type": "text", "text": "[Request interrupted by user for tool use]"})
        (unica,) = reg.ocorrencias_claude(junto)
        self.assertEqual((unica["chave"], unica["tool_use_id"]), ("toolu_J", "toolu_J"))

    def test_09_tres_tool_results_na_mesma_linha(self):
        linha = falha("toolu_X1")
        linha["message"]["content"] = [resultado("toolu_X1"), resultado("toolu_X2", "ok", erro=False),
                                       resultado("toolu_X3", "Exit code 1")]
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        inicio = os.path.getsize(self.arq)
        self.escrever(self.arq, linha)
        ocs = list(self.passada()["ocorrencias"].values())
        self.assertEqual(len(ocs), 2)
        self.assertEqual({o["chave"] for o in ocs}, {"toolu_X1", "toolu_X3"})
        self.assertEqual([o["offset"] for o in ocs], [inicio, inicio])
        self.assertNotEqual(ocs[0]["id"], ocs[1]["id"])

    def test_10_mesmo_tool_use_id_em_outro_arquivo(self):
        outro = self.claude / "proj" / "outra-sessao.jsonl"
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, falha("toolu_D"))
        self.assertEqual(len(self.passada()["ocorrencias"]), 1)
        self.escrever(outro, falha("toolu_D", hora=T + 50))
        estado = self.passada(T + 200)
        self.assertEqual(len(estado["ocorrencias"]), 1)
        (oc,) = estado["ocorrencias"].values()
        self.assertEqual((oc["arquivo"], oc["hora"]), (str(self.arq), T + 5))
        self.assertIn(str(outro), estado["arquivos"])

    def test_11_ultima_linha_sem_quebra_nao_consumida(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        antes = os.path.getsize(self.arq)
        self.escrever(self.arq, jl(falha("toolu_I"))[:-1])
        self.assertEqual(self.passada()["ocorrencias"], {})
        self.assertEqual(self.disco()["arquivos"][str(self.arq)]["offset"], antes)
        self.escrever(self.arq, b"\n")
        self.assertEqual(set(self.por_chave(self.passada(T + 150))), {"toolu_I"})
        self.assertEqual(self.disco()["arquivos"][str(self.arq)]["offset"], os.path.getsize(self.arq))
        self.assertEqual(len(self.passada(T + 200)["ocorrencias"]), 1)

    def test_12_arquivo_truncado_relido_sem_duplicar(self):
        self.escrever(self.arq, {"type": "system", "texto": "x" * 2000})
        self.marcar()
        self.escrever(self.arq, falha("toolu_E1"))
        self.assertEqual(len(self.passada()["ocorrencias"]), 1)
        guardado = self.disco()["arquivos"][str(self.arq)]["offset"]
        conteudo = jl(falha("toolu_E1")) + jl(falha("toolu_E2", hora=T + 6))
        self.assertLess(len(conteudo), guardado)
        self.escrever(self.arq, conteudo, modo="wb")
        estado = self.passada(T + 150)
        self.assertEqual(len(estado["ocorrencias"]), 2)
        self.assertEqual(set(self.por_chave(estado)), {"toolu_E1", "toolu_E2"})
        self.assertEqual(self.disco()["arquivos"][str(self.arq)]["offset"], len(conteudo))

    def test_13_linha_gigante_descartada(self):
        def gigante(tid, total):
            base = jl(falha(tid, texto=""))
            return jl(falha(tid, texto="x" * (total - len(base))))

        with patch.object(reg, "MAX_LINHA", 1000):
            self.escrever(self.arq, {"type": "system"})
            self.marcar()
            linha = gigante("toolu_G1", 5000)
            self.assertEqual(len(linha), 5000)
            self.escrever(self.arq, linha, falha("toolu_N1"))
            self.assertEqual(set(self.por_chave(self.passada())), {"toolu_N1"})
            antes = os.path.getsize(self.arq)
            pedaco = gigante("toolu_G2", 5001)[:-1]
            self.assertEqual(len(pedaco), 5000)
            self.escrever(self.arq, pedaco)
            self.assertEqual(set(self.por_chave(self.passada(T + 150))), {"toolu_N1"})
            self.assertEqual(self.disco()["arquivos"][str(self.arq)]["offset"], antes)
            self.escrever(self.arq, b"\n", falha("toolu_N2"))
            self.assertEqual(set(self.por_chave(self.passada(T + 200))), {"toolu_N1", "toolu_N2"})
            self.assertEqual(self.disco()["arquivos"][str(self.arq)]["offset"], os.path.getsize(self.arq))

    def test_14_orcamento_de_bytes_divide_entre_passadas(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        linhas = [jl(falha("toolu_B%d" % i)) for i in range(1, 5)]
        tamanho = len(linhas[0])
        self.assertTrue(all(len(x) == tamanho for x in linhas))
        self.escrever(self.arq, *linhas)
        with patch.object(reg, "ORCAMENTO_BYTES", 2 * tamanho - 1):
            n1 = len(self.passada()["ocorrencias"])
            n2 = len(self.passada(T + 150)["ocorrencias"])
        self.assertTrue(0 < n1 < 4)
        self.assertEqual(n1, 2)
        self.assertEqual(n2, 4)

    def test_15_fora_da_janela_e_open_negado(self):
        velho = self.claude / "velho" / "antigo.jsonl"
        negado = self.claude / "proj" / "negado.jsonl"
        antigo = T - reg.JANELA_ARQUIVO_S - 100
        self.escrever(velho, {"type": "system"}, mtime=antigo)
        self.escrever(self.arq, {"type": "system"})
        self.escrever(negado, {"type": "system"})
        self.marcar()
        marcado = self.disco()
        self.assertNotIn(str(velho), marcado["arquivos"])
        self.escrever(velho, falha("toolu_V"), mtime=antigo)
        self.escrever(self.arq, falha("toolu_OK"))
        self.escrever(negado, falha("toolu_NEG"))
        real = builtins.open

        def falso(arquivo, *args, **kwargs):
            if isinstance(arquivo, (str, os.PathLike)) and os.fspath(arquivo) == str(negado):
                raise PermissionError(13, "acesso negado")
            return real(arquivo, *args, **kwargs)

        with patch("builtins.open", falso):
            estado = self.passada()
        self.assertEqual(set(self.por_chave(estado)), {"toolu_OK"})
        self.assertEqual(estado["arquivos"][str(negado)], marcado["arquivos"][str(negado)])
        self.assertNotIn(str(velho), estado["arquivos"])
        self.assertEqual(set(self.por_chave(self.passada(T + 150))), {"toolu_OK", "toolu_NEG"})

    def test_16_hora_anterior_a_marca_ignorada(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        novo = self.claude / "proj" / "novo.jsonl"
        self.escrever(novo, falha("toolu_ANTES", hora=T - 100), falha("toolu_DEPOIS", hora=T + 5))
        self.assertEqual(set(self.por_chave(self.passada())), {"toolu_DEPOIS"})

    def test_17_hora_futura_e_invalida_viram_agora(self):
        agora = T + 100
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        sem_hora = falha("toolu_SEM")
        del sem_hora["timestamp"]
        self.escrever(self.arq, falha("toolu_FUT", hora=agora + reg.TOLERANCIA_FUTURO_S + 1000),
                      falha("toolu_PERTO", hora=agora + 200), falha("toolu_INV", timestamp="ontem às três"),
                      falha("toolu_NAIVE", timestamp="2026-11-01T10:00:00"), falha("toolu_NUM", timestamp=T + 20),
                      sem_hora)
        por = self.por_chave(self.passada(agora))
        self.assertEqual(por["toolu_FUT"]["hora"], agora)
        self.assertEqual(por["toolu_INV"]["hora"], agora)
        self.assertEqual(por["toolu_NAIVE"]["hora"], agora)
        self.assertEqual(por["toolu_SEM"]["hora"], agora)
        self.assertEqual(por["toolu_PERTO"]["hora"], agora + 200)
        self.assertEqual(por["toolu_NUM"]["hora"], T + 20)

    def test_18_subagentes_capturados_outras_pastas_nao(self):
        sub = self.claude / "proj" / "uuid-1" / "subagents" / "agent-x.jsonl"
        fora = self.claude / "proj" / "uuid-1" / "outra" / "x.jsonl"
        self.escrever(sub, {"type": "system"})
        self.escrever(fora, {"type": "system"})
        self.marcar()
        self.assertIn(str(sub), self.disco()["arquivos"])
        self.assertNotIn(str(fora), self.disco()["arquivos"])
        self.escrever(sub, falha("toolu_SUB"))
        self.escrever(fora, falha("toolu_FORA"))
        estado = self.passada()
        self.assertEqual(set(self.por_chave(estado)), {"toolu_SUB"})
        oc = self.por_chave(estado)["toolu_SUB"]
        self.assertEqual((oc["agente"], oc["arquivo"]), ("claude", str(sub)))
        self.assertNotIn(str(fora), estado["arquivos"])

    def test_19_codex_command_execution(self):
        self.marcar()
        concluido = evento(item_codex("call-ok", status="completed", ultimo="failed"))
        self.assertIn(b'"failed"', jl(concluido))
        self.assertEqual(reg.ocorrencias_codex(concluido), [])
        meta = {"timestamp": iso(T + 1), "type": "session_meta", "payload": {"id": "thread-1"}}
        self.escrever(self.rollout, meta, evento(item_codex("call-1")), concluido)
        estado = self.passada()
        self.assertEqual(len(estado["ocorrencias"]), 1)
        (oc,) = estado["ocorrencias"].values()
        self.assertEqual(oc, {"id": reg.id_de("codex", "thread-1:call-1"), "agente": "codex", "ferramenta": "comando",
                              "classe": "codigo_saida", "codigo": 1, "hora": T + 7, "arquivo": str(self.rollout),
                              "offset": len(jl(meta)), "chave": "thread-1:call-1"})

    def test_20_codex_mcp_filechange_e_outro_tipo(self):
        casos = [("McpToolCall", ("mcp", "outra")), ("FileChange", ("arquivo", "outra")),
                 ("WebSearch", ("outra", "outra"))]
        for tipo, (ferramenta, classe) in casos:
            with self.subTest(tipo):
                (o,) = reg.ocorrencias_codex(evento(item_codex("call-x", tipo=tipo)))
                self.assertEqual((o["ferramenta"], o["classe"], o["codigo"]), (ferramenta, classe, None))
                self.assertEqual((o["chave"], o["hora"]), ("thread-1:call-x", T + 7))
        for saida in (True, "1", 2 ** 40, None):
            with self.subTest(exit_code=saida):
                (o,) = reg.ocorrencias_codex(evento(item_codex("call-y", exit_code=saida)))
                self.assertIsNone(o["codigo"])
        self.assertEqual(reg.ocorrencias_codex(evento(item_codex("call-z"), thread="")), [])
        self.assertEqual(reg.ocorrencias_codex(evento(item_codex(""))), [])

    def test_21_codex_turn_aborted(self):
        (o,) = reg.ocorrencias_codex(aborto())
        self.assertEqual(o, {"chave": "abort:turn-9", "ferramenta": "nenhuma", "classe": "interrompido",
                             "codigo": None, "hora": T + 8})
        (o,) = reg.ocorrencias_codex(aborto(motivo="replaced"))
        self.assertEqual(o["classe"], "outra")
        self.assertEqual(reg.ocorrencias_codex(aborto(turno="")), [])
        self.marcar()
        self.escrever(self.rollout, aborto())
        self.assertEqual(self.por_chave(self.passada())["abort:turn-9"]["classe"], "interrompido")

    @unittest.skipUnless(reg.zstd is not None, "sem compression.zstd")
    def test_22_zst_novo_processado_uma_vez(self):
        self.marcar()
        meta = jl({"timestamp": iso(T + 1), "type": "session_meta", "payload": {}})
        self.zst.parent.mkdir(parents=True)
        self.zst.write_bytes(reg.zstd.compress(meta + jl(evento(item_codex("call-z")))))
        os.utime(self.zst, (MTIME, MTIME))
        with patch.object(reg.zstd, "open", wraps=reg.zstd.open) as espia:
            estado = self.passada()
        self.assertEqual(espia.call_count, 1)
        (oc,) = estado["ocorrencias"].values()
        self.assertEqual(oc["arquivo"], str(self.rollout))
        self.assertEqual(oc["offset"], len(meta))
        self.assertEqual(estado["arquivos"][str(self.zst)]["tam"], self.zst.stat().st_size)
        with patch.object(reg.zstd, "open", wraps=reg.zstd.open) as espia:
            estado = self.passada(T + 150)
        self.assertEqual(espia.call_count, 0)
        self.assertEqual(len(estado["ocorrencias"]), 1)

    @unittest.skipUnless(reg.zstd is not None, "sem compression.zstd")
    def test_23_jsonl_que_virou_zst_nao_duplica(self):
        self.marcar()
        conteudo = jl(evento(item_codex("call-b")))
        self.escrever(self.rollout, conteudo)
        primeiro = self.passada()
        self.assertEqual(len(primeiro["ocorrencias"]), 1)
        self.zst.write_bytes(reg.zstd.compress(conteudo))
        os.utime(self.zst, (MTIME, MTIME))
        os.remove(self.rollout)
        estado = self.passada(T + 150)
        self.assertEqual(set(estado["ocorrencias"]), set(primeiro["ocorrencias"]))
        self.assertIn(str(self.zst), estado["arquivos"])
        self.assertNotIn(str(self.rollout), estado["arquivos"])

    def test_24_sem_zstd_ignora_zst(self):
        self.zst.parent.mkdir(parents=True)
        self.zst.write_bytes(b"isto nao e zstd")
        os.utime(self.zst, (MTIME, MTIME))
        with patch.object(reg, "zstd", None):
            self.marcar()
            self.assertNotIn(str(self.zst), self.disco()["arquivos"])
            self.zst.write_bytes(b"mais bytes que nao sao zstd")
            os.utime(self.zst, (MTIME, MTIME))
            estado, erro = reg.varrer(self.dados, T + 100, self.claude, self.codex)
        self.assertIsNone(erro)
        self.assertEqual(estado["ocorrencias"], {})
        self.assertNotIn(str(self.zst), estado["arquivos"])

    def test_25_estado_invalido_falha_fechado(self):
        self.escrever(self.arq, {"type": "system"}, falha("toolu_A"))
        self.dados.mkdir()
        alvo = self.dados / reg.ARQ_OCORRENCIAS
        conteudos = (b"{nao e json", b"[]", b"null",
                     b'{"versao":2,"criado_em":1,"arquivos":{},"ocorrencias":{}}',
                     b'{"versao":1,"criado_em":1,"arquivos":[],"ocorrencias":{}}',
                     b'{"versao":1,"criado_em":1,"arquivos":{},"ocorrencias":[]}',
                     b'{"versao":1,"arquivos":{},"ocorrencias":{}}')
        for conteudo in conteudos:
            with self.subTest(conteudo=conteudo):
                alvo.write_bytes(conteudo)
                self.assertEqual(reg.varrer(self.dados, T + 100, self.claude, self.codex), (None, reg.ERRO_ESTADO))
                self.assertEqual(alvo.read_bytes(), conteudo)
                with self.assertRaises(reg.ErroEstado):
                    reg.carregar_ocorrencias(self.dados)

    def test_26_limpeza_por_retencao_e_teto(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, *[falha("toolu_R%d" % i, hora=T + i) for i in range(1, 6)])
        with patch.object(reg, "MAX_OCORRENCIAS", 3):
            estado = self.passada()
        self.assertEqual(sorted(o["hora"] for o in estado["ocorrencias"].values()), [T + 3, T + 4, T + 5])
        estado = self.passada(T + reg.RETENCAO_S + 4.5)
        self.assertEqual(set(self.por_chave(estado)), {"toolu_R5"})
        self.assertEqual(set(self.disco()["ocorrencias"]), {reg.id_de("claude", "toolu_R5")})

    def test_27_replace_negado_nao_derruba_a_passada(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, falha("toolu_P"))
        with patch.object(reg.os, "replace", side_effect=PermissionError(13, "em uso")) as troca:
            estado, erro = reg.varrer(self.dados, T + 100, self.claude, self.codex)
        self.assertIsNone(erro)
        self.assertEqual(set(self.por_chave(estado)), {"toolu_P"})
        self.assertEqual(troca.call_count, 6)
        self.assertEqual([n for n in os.listdir(self.dados) if n.endswith(".tmp")], [])
        self.assertEqual(self.disco()["ocorrencias"], {})

    def test_28_estado_em_disco_sem_texto_do_transcript(self):
        self.escrever(self.arq, {"type": "system"})
        self.escrever(self.rollout, {"type": "session_meta"})
        self.marcar()
        self.escrever(self.arq, uso("toolu_M", comando="rm SEGREDO-COMANDO"),
                      falha("toolu_M", texto="Exit code 1\nSEGREDO-ERRO em C:/SEGREDO-PASTA"),
                      {"type": "assistant", "uuid": "api-m", "timestamp": iso(T + 6), "isApiErrorMessage": True,
                       "error": "SEGREDO-ERRO", "apiErrorStatus": 500})
        self.escrever(self.rollout, evento(item_codex("call-m")), aborto(motivo="SEGREDO-ERRO"))
        estado = self.passada()
        self.assertEqual(len(estado["ocorrencias"]), 4)
        bruto = (self.dados / reg.ARQ_OCORRENCIAS).read_bytes()
        for marca in MARCAS:
            self.assertNotIn(marca.encode("ascii"), bruto)
        campos = {"id", "agente", "ferramenta", "classe", "codigo", "hora", "arquivo", "offset", "chave"}
        for oc in json.loads(bruto)["ocorrencias"].values():
            self.assertEqual(set(oc), campos)

    def test_29_transcripts_so_lidos(self):
        self.escrever(self.arq, {"type": "system"})
        self.escrever(self.rollout, {"type": "session_meta"})
        self.marcar()
        self.escrever(self.arq, uso("toolu_S"), falha("toolu_S"))
        self.escrever(self.rollout, evento(item_codex("call-s")))
        antes = (retrato(self.claude), retrato(self.codex))
        estado = self.passada()
        textos = [reg.ler_texto(oc) for oc in estado["ocorrencias"].values()]
        self.assertEqual(len(textos), 2)
        self.assertTrue(all(t is not None for t in textos))
        self.passada(T + 150)
        self.assertEqual((retrato(self.claude), retrato(self.codex)), antes)

    def test_30_estado_de(self):
        agora = T + 10 * 86400
        ident = "0123abcdef"

        def oc(classe, hora):
            return {"id": ident, "agente": "claude", "ferramenta": "bash", "classe": classe, "codigo": None,
                    "hora": hora}

        recente = agora - 100
        velho = agora - reg.PRAZO_ABERTO_S - 1
        corrigido = {ident: {"estado": "corrigido", "corrigido_em": agora - 50, "nota": None,
                             "verificado_em": None, "evidencia": None}}
        verificado = {ident: {"estado": "verificado", "corrigido_em": agora - 50, "nota": None,
                              "verificado_em": agora - 40, "evidencia": None}}
        for classe in reg.SO_CONTAM:
            for registros in ({}, corrigido, verificado):
                for hora in (recente, velho):
                    with self.subTest(classe=classe, registros=registros, hora=hora):
                        self.assertEqual(reg.estado_de(oc(classe, hora), registros, agora), "contado")
        casos = [("recente", recente, {}, "aberto"), ("no limite", agora - reg.PRAZO_ABERTO_S, {}, "aberto"),
                 ("velho", velho, {}, "sem_tratamento"), ("corrigido", recente, corrigido, "corrigido"),
                 ("corrigido velho", velho, corrigido, "corrigido"), ("verificado", recente, verificado, "verificado"),
                 ("verificado velho", velho, verificado, "verificado"),
                 ("registro malformado", recente, {ident: {"estado": "talvez"}}, "aberto"),
                 ("registro lixo", velho, {ident: "lixo"}, "sem_tratamento")]
        for nome, hora, registros, esperado in casos:
            with self.subTest(nome):
                self.assertEqual(reg.estado_de(oc("codigo_saida", hora), registros, agora), esperado)

    def test_31_resumo_publico(self):
        agora = T + 10 * 86400

        def oc(ident, hora, classe="codigo_saida", agente="claude", ferramenta="bash", codigo=2):
            return {"id": ident, "agente": agente, "ferramenta": ferramenta, "classe": classe, "codigo": codigo,
                    "hora": hora, "arquivo": "x.jsonl", "offset": 0, "chave": "k-" + ident}

        def ev(tipo, codigo):
            return {"tipo": tipo, "comando": "SEGREDO-COMANDO", "codigo": codigo,
                    "arquivo": "erros-evidencias/x.txt", "sha256": "0" * 64, "bytes": 3}

        lista = [
            oc("a000000001", agora - 100),
            oc("a000000002", agora - 200, agente="codex", ferramenta="comando", codigo=1),
            oc("a000000003", agora - 2 * 86400, classe="edicao", ferramenta="edit", codigo=None),
            oc("a000000004", agora - 300),
            oc("5000000001", agora - 8 * 86400),
            oc("c000000001", agora - 2000, classe="arquivo", ferramenta="write", codigo=None),
            oc("e000000001", agora - 3000, classe="tempo_esgotado", codigo=None),
            oc("e000000002", agora - 4000, classe="validacao", ferramenta="read", codigo=None),
            oc("e000000003", agora - 90000, classe="outra", ferramenta="outra", codigo=None),
            oc("d000000001", agora - 400, classe="permissao_negada", codigo=None),
            oc("d000000002", agora - 500, classe="interrompido", ferramenta="nenhuma", codigo=None),
            oc("d000000003", agora - 600, classe="api", ferramenta="nenhuma", codigo=429),
            oc("f000000001", agora - 100, classe="inventada"),
            oc("f000000002", agora - 100, agente="grok"),
            oc("f000000003", agora - 100, ferramenta="martelo"),
        ]
        estado = {"versao": 1, "criado_em": T, "arquivos": {}, "ocorrencias": {o["id"]: o for o in lista}}
        registros = {
            "a000000004": {"estado": "talvez"},
            "c000000001": {"estado": "corrigido", "corrigido_em": agora - 50, "nota": "SEGREDO-ERRO",
                           "verificado_em": None, "evidencia": ev("teste", 0)},
            "e000000001": {"estado": "verificado", "corrigido_em": agora - 60, "nota": None,
                           "verificado_em": agora - 10, "evidencia": ev("teste", 0)},
            "e000000002": {"estado": "verificado", "corrigido_em": agora - 60, "nota": None,
                           "verificado_em": agora - 20, "evidencia": ev("foto", 0)},
            "e000000003": {"estado": "verificado", "corrigido_em": agora - 60, "nota": None,
                           "verificado_em": agora - 30, "evidencia": ev("comando", "0")},
            "d000000002": {"estado": "verificado", "corrigido_em": agora - 6, "nota": None,
                           "verificado_em": agora - 5, "evidencia": ev("teste", 0)},
            "b000000009": "lixo",
        }
        resumo = reg.resumo_publico(estado, registros, agora)
        self.assertEqual((resumo["estado"], resumo["desde"]), ("ok", T))
        self.assertEqual(resumo["contagens"], {"aberto": 4, "corrigido": 1, "verificado": 3, "sem_tratamento": 1,
                                               "contado": 3})
        self.assertEqual(list(resumo["por_classe"]), list(reg.CLASSES))
        self.assertEqual(resumo["por_classe"], {"permissao_negada": 1, "interrompido": 1, "codigo_saida": 4,
                                                "edicao": 1, "arquivo": 1, "tempo_esgotado": 1, "validacao": 1,
                                                "api": 1, "outra": 1})
        self.assertEqual(resumo["por_agente"], {"claude": 11, "codex": 1})
        self.assertEqual(resumo["ultimas_24h"], 9)
        self.assertEqual([i["id"] for i in resumo["itens"]],
                         ["a000000001", "a000000002", "a000000004", "a000000003",
                          "e000000001", "e000000002", "e000000003", "c000000001"])
        self.assertEqual(resumo["itens"][0], {"id": "a000000001", "agente": "claude", "ferramenta": "bash",
                                              "classe": "codigo_saida", "codigo": 2, "hora": agora - 100,
                                              "estado": "aberto", "evidencia": None})
        evidencias = {i["id"]: i["evidencia"] for i in resumo["itens"]}
        self.assertEqual(evidencias.pop("e000000001"), {"tipo": "teste", "codigo": 0})
        self.assertEqual(set(evidencias.values()), {None})
        with patch.object(reg, "MAX_ABERTOS_PUBLICOS", 2), patch.object(reg, "MAX_ITENS_PUBLICOS", 3):
            pequeno = reg.resumo_publico(estado, registros, agora)
        self.assertEqual([i["id"] for i in pequeno["itens"]], ["a000000001", "a000000002", "e000000001"])
        self.assertEqual(pequeno["contagens"], resumo["contagens"])
        with patch.object(reg, "MAX_ABERTOS_PUBLICOS", 2), patch.object(reg, "MAX_ITENS_PUBLICOS", 2):
            self.assertEqual([i["id"] for i in reg.resumo_publico(estado, registros, agora)["itens"]],
                             ["a000000001", "a000000002"])

    def test_32_resumo_sem_texto_nem_ponteiro(self):
        self.escrever(self.arq, {"type": "system"})
        self.escrever(self.rollout, {"type": "session_meta"})
        self.marcar()
        self.escrever(self.arq, uso("toolu_P1", comando="cat SEGREDO-COMANDO"),
                      falha("toolu_P1", texto="Exit code 1\nSEGREDO-ERRO C:/SEGREDO-PASTA"),
                      uso("toolu_P2"), falha("toolu_P2"))
        self.escrever(self.rollout, evento(item_codex("call-p")))
        estado = self.passada()
        saida = self.base / "evidencia.txt"
        saida.write_bytes("SEGREDO-ERRO saída".encode("utf-8"))
        reg.registrar_corrigido(self.dados, reg.id_de("claude", "toolu_P2"), "nota SEGREDO-ERRO", T + 200)
        reg.registrar_verificado(self.dados, reg.id_de("codex", "thread-1:call-p"), "comando",
                                 "SEGREDO-COMANDO --x", 0, str(saida), T + 210)
        resumo = reg.resumo_publico(estado, reg.carregar_registro(self.dados, True), T + 300)
        self.assertEqual((resumo["contagens"]["aberto"], resumo["contagens"]["corrigido"],
                          resumo["contagens"]["verificado"]), (1, 1, 1))
        self.assertEqual(len(resumo["itens"]), 3)
        for item in resumo["itens"]:
            self.assertEqual(set(item), {"id", "agente", "ferramenta", "classe", "codigo", "hora", "estado",
                                         "evidencia"})
            self.assertRegex(item["id"], RE_ID)
        texto = json.dumps(resumo, ensure_ascii=False)
        for marca in MARCAS:
            self.assertNotIn(marca, texto)
        for forma in (str(self.base), json.dumps(str(self.base))[1:-1], self.base.as_posix()):
            self.assertNotIn(forma, texto)
        # "arquivo" é também classe de lista fixa (chave de por_classe) e "comando" é valor de ferramenta:
        # as chaves proibidas são procuradas em todos os dicts do resumo, menos dentro de por_classe.
        def chaves(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield k
                    if k != "por_classe":
                        yield from chaves(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from chaves(v)

        self.assertEqual(list(resumo["por_classe"]), list(reg.CLASSES))
        self.assertEqual(set(chaves(resumo)) & {"arquivo", "offset", "chave", "nota", "comando"}, set())

    def test_33_ler_texto_claude(self):
        texto = "Exit code 2\n" + "SEGREDO-ERRO linha longa\n" * 40
        self.assertGreater(len(texto), 400)
        self.escrever(self.arq, {"type": "system", "x": 1})
        self.marcar()
        self.escrever(self.arq, uso("toolu_T", comando="grep SEGREDO-COMANDO ."), falha("toolu_T", texto=texto))
        estado = self.passada()
        oc = estado["ocorrencias"][reg.id_de("claude", "toolu_T")]
        esperado = {"erro": texto, "entrada": json.dumps({"command": "grep SEGREDO-COMANDO ."}, ensure_ascii=False),
                    "pasta": "C:/proj/SEGREDO-PASTA", "sessao": "sessao-1"}
        self.assertEqual(reg.ler_texto(oc), esperado)
        self.assertEqual(reg.ler_texto(dict(oc, offset=0)), esperado)
        self.assertEqual(reg.ler_texto(dict(oc, offset=oc["offset"] + 3)), esperado)
        self.assertIsNone(reg.ler_texto(dict(oc, chave="toolu_OUTRO", offset=0)))
        os.remove(self.arq)
        self.assertIsNone(reg.ler_texto(oc))

    def test_34_ler_texto_codex(self):
        self.marcar()
        mcp = {"type": "McpToolCall", "id": "call-3", "status": "failed", "result": {"erro": "SEGREDO-ERRO"}}
        self.escrever(self.rollout, {"timestamp": iso(T + 1), "type": "session_meta", "payload": {}},
                      evento(item_codex("call-1", command=["bash", "-lc", "SEGREDO-COMANDO"])),
                      evento(item_codex("call-2")), evento(mcp), aborto())
        estado = self.passada()
        por = self.por_chave(estado)
        esperado1 = {"erro": "SEGREDO-ERRO: falhou", "entrada": "bash -lc SEGREDO-COMANDO",
                     "pasta": "/home/x/SEGREDO-PASTA", "sessao": "thread-1"}
        esperado2 = dict(esperado1, entrada="bash -lc 'echo SEGREDO-COMANDO'")
        esperado3 = {"erro": json.dumps({"erro": "SEGREDO-ERRO"}, ensure_ascii=False), "entrada": "", "pasta": "",
                     "sessao": "thread-1"}
        self.assertEqual(reg.ler_texto(por["thread-1:call-1"]), esperado1)
        self.assertEqual(reg.ler_texto(por["thread-1:call-2"]), esperado2)
        self.assertEqual(reg.ler_texto(por["thread-1:call-3"]), esperado3)
        self.assertEqual(reg.ler_texto(por["abort:turn-9"])["erro"], "interrupted")
        if reg.zstd is not None:
            self.zst.write_bytes(reg.zstd.compress(self.rollout.read_bytes()))
            os.remove(self.rollout)
            self.assertEqual(reg.ler_texto(por["thread-1:call-1"]), esperado1)
            self.assertEqual(reg.ler_texto(dict(por["thread-1:call-2"], offset=0)), esperado2)

    def test_35_registrar_corrigido(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, uso("toolu_A"), falha("toolu_A"),
                      falha("toolu_N", texto="Permission to use Bash has been denied.", toolDenialKind="permission-rule"))
        self.passada()
        ida = reg.id_de("claude", "toolu_A")
        idn = reg.id_de("claude", "toolu_N")
        reg.registrar_corrigido(self.dados, ida, "ajustei o caminho", T + 200)
        self.assertEqual(self.registro_disco(), {"versao": 1, "registros": {ida: {
            "estado": "corrigido", "corrigido_em": T + 200, "nota": "ajustei o caminho", "verificado_em": None,
            "evidencia": None}}})
        alvo = self.dados / reg.ARQ_REGISTRO
        antes = alvo.read_bytes()
        casos = [(ida, "já está corrigido"),
                 (idn, "esta ocorrência só é contada (classe permissao_negada); não entra no fluxo de correção"),
                 ("0123456789", "id desconhecido"), ("XYZ", "id inválido"), ("0123456789\n", "id inválido"),
                 ("../0123456", "id inválido"), ("0123ABCDEF", "id inválido"), (None, "id inválido")]
        for ident, mensagem in casos:
            with self.subTest(ident=ident):
                with self.assertRaises(reg.ErroRegistro) as erro:
                    reg.registrar_corrigido(self.dados, ident, None, T + 300)
                self.assertEqual(str(erro.exception), mensagem)
                self.assertEqual(alvo.read_bytes(), antes)
        (item,) = [i for i in reg.listar(self.dados, T + 300) if i["id"] == ida]
        self.assertEqual((item["estado"], item["nota"], item["corrigido_em"], item["verificado_em"]),
                         ("corrigido", "ajustei o caminho", T + 200, None))

    def test_36_registrar_verificado(self):
        ida, idb, idc = self.tres_abertos()
        alvo = self.dados / reg.ARQ_REGISTRO
        pasta_ev = self.dados / reg.DIR_EVIDENCIAS
        vazia = self.base / "vazia.txt"
        vazia.write_bytes(b"")
        grande = self.base / "grande.txt"
        grande.write_bytes(b"x" * (reg.MAX_EVIDENCIA + 1))
        invalidos = [(dict(caminho_saida=str(self.base / "nao-existe.txt")), "--saida: arquivo ausente"),
                     (dict(caminho_saida=str(self.base)), "--saida: arquivo ausente"),
                     (dict(caminho_saida=str(vazia)), "--saida: arquivo vazio"),
                     (dict(caminho_saida=str(grande)), "--saida: arquivo maior que 262144 bytes"),
                     (dict(tipo="foto"), "--tipo: use teste ou comando"),
                     (dict(codigo="0"), "--codigo: informe um inteiro"),
                     (dict(codigo=True), "--codigo: informe um inteiro"),
                     (dict(codigo=1.5), "--codigo: informe um inteiro"),
                     (dict(codigo=reg.MAX_CODIGO + 1), "--codigo: informe um inteiro"),
                     (dict(codigo=reg.MIN_CODIGO - 1), "--codigo: informe um inteiro")]
        saida = self.base / "saida.txt"
        saida.write_bytes(b"Ran 3 tests\nOK\n")
        for mudanca, mensagem in invalidos:
            with self.subTest(mensagem=mensagem, mudanca=mudanca):
                args = dict(tipo="teste", comando="py -m unittest", codigo=0, caminho_saida=str(saida))
                args.update(mudanca)
                with self.assertRaises(reg.ErroRegistro) as erro:
                    reg.registrar_verificado(self.dados, idc, args["tipo"], args["comando"], args["codigo"],
                                             args["caminho_saida"], T + 200)
                self.assertEqual(str(erro.exception), mensagem)
                self.assertFalse(alvo.exists())
                self.assertFalse(pasta_ev.exists())
        reg.registrar_verificado(self.dados, ida, "teste", "py -m unittest", 0, str(saida), T + 200)
        dados_saida = saida.read_bytes()
        self.assertEqual(self.registro_disco()["registros"][ida], {
            "estado": "verificado", "corrigido_em": T + 200, "nota": None, "verificado_em": T + 200,
            "evidencia": {"tipo": "teste", "comando": "py -m unittest", "codigo": 0,
                          "arquivo": "erros-evidencias/" + ida + ".txt",
                          "sha256": hashlib.sha256(dados_saida).hexdigest(), "bytes": len(dados_saida)}})
        copia = pasta_ev / (ida + ".txt")
        self.assertEqual(copia.read_bytes(), dados_saida)
        reg.registrar_corrigido(self.dados, idb, "nota b", T + 250)
        reg.registrar_verificado(self.dados, idb, "comando", "rg x", 1, str(saida), T + 300)
        b = self.registro_disco()["registros"][idb]
        self.assertEqual((b["estado"], b["corrigido_em"], b["nota"], b["verificado_em"], b["evidencia"]["codigo"],
                          b["evidencia"]["tipo"]), ("verificado", T + 250, "nota b", T + 300, 1, "comando"))
        antes = alvo.read_bytes()
        outra = self.base / "outra.txt"
        outra.write_bytes(b"outra evidencia")
        with self.assertRaises(reg.ErroRegistro) as erro:
            reg.registrar_verificado(self.dados, ida, "teste", "py -m unittest", 0, str(outra), T + 400)
        self.assertEqual(str(erro.exception), "já está verificado")
        with self.assertRaises(reg.ErroRegistro) as erro:
            reg.registrar_corrigido(self.dados, idb, None, T + 400)
        self.assertEqual(str(erro.exception), "já está verificado")
        self.assertEqual(copia.read_bytes(), dados_saida)
        self.assertEqual(alvo.read_bytes(), antes)
        self.assertFalse((pasta_ev / (idc + ".txt")).exists())

    def test_37_trava_com_tempo_limite_e_retomada(self):
        ida, idb, _idc = self.tres_abertos()
        trava = self.dados / reg.ARQ_TRAVA
        trava.write_bytes(b"")
        with patch.object(reg, "ESPERA_TRAVA_S", 0.2):
            inicio = time.monotonic()
            with self.assertRaises(reg.ErroEstado) as erro:
                reg.registrar_corrigido(self.dados, ida, None, T + 200)
            self.assertLess(time.monotonic() - inicio, 3)
        self.assertEqual(str(erro.exception), "registro ocupado; tente de novo")
        self.assertTrue(trava.exists())
        self.assertFalse((self.dados / reg.ARQ_REGISTRO).exists())
        velho = time.time() - reg.TRAVA_VELHA_S - 30
        os.utime(trava, (velho, velho))
        reg.registrar_corrigido(self.dados, ida, None, T + 200)
        self.assertFalse(trava.exists())
        self.assertIn(ida, self.registro_disco()["registros"])
        saida = self.base / "saida.txt"
        saida.write_bytes(b"ok\n")
        reg.registrar_verificado(self.dados, idb, "teste", "py -m unittest", 0, str(saida), T + 210)
        self.assertFalse(trava.exists())
        with self.assertRaises(reg.ErroRegistro):
            reg.registrar_corrigido(self.dados, ida, None, T + 220)
        self.assertFalse(trava.exists())

    def test_38_registro_vencido_removido_com_evidencia(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, falha("toolu_A"))
        self.passada()
        ida = reg.id_de("claude", "toolu_A")
        saida = self.base / "saida.txt"
        saida.write_bytes(b"ok\n")
        reg.registrar_verificado(self.dados, ida, "teste", "py -m unittest", 0, str(saida), T + 200)
        copia = self.dados / reg.DIR_EVIDENCIAS / (ida + ".txt")
        self.assertTrue(copia.exists())
        depois = T + reg.RETENCAO_S
        self.escrever(self.arq, falha("toolu_B", hora=depois + 500), mtime=depois + 400)
        estado = self.passada(depois + 1000)
        self.assertEqual(set(self.por_chave(estado)), {"toolu_B"})
        idb = reg.id_de("claude", "toolu_B")
        reg.registrar_corrigido(self.dados, idb, None, depois + 1100)
        self.assertEqual(set(self.registro_disco()["registros"]), {idb})
        self.assertFalse(copia.exists())

    def test_39_registro_invalido_falha_fechado(self):
        ida, _idb, _idc = self.tres_abertos()
        alvo = self.dados / reg.ARQ_REGISTRO
        for conteudo in (b"{lixo", b"[]", b'{"versao":2,"registros":{}}', b'{"versao":1,"registros":[]}'):
            with self.subTest(conteudo=conteudo):
                alvo.write_bytes(conteudo)
                self.assertEqual(reg.carregar_registro(self.dados, True), {})
                with self.assertRaises(reg.ErroEstado):
                    reg.carregar_registro(self.dados, False)
                with self.assertRaises(reg.ErroEstado) as erro:
                    reg.registrar_corrigido(self.dados, ida, None, T + 200)
                self.assertEqual(str(erro.exception), "estado local ilegível")
                self.assertEqual(alvo.read_bytes(), conteudo)
                with self.assertRaises(reg.ErroEstado):
                    reg.listar(self.dados, T + 200)
        bom = {"estado": "corrigido", "corrigido_em": T + 1, "nota": None, "verificado_em": None, "evidencia": None}
        alvo.write_bytes(json.dumps({"versao": 1, "registros": {ida: bom, "XYZ": bom, "abcdef0123": "lixo",
                                                                "abcdef0124": {"estado": "talvez"}}}).encode("ascii"))
        self.assertEqual(reg.carregar_registro(self.dados, False), {ida: bom})

    def test_40_id_de_e_diretorio_dados(self):
        ident = reg.id_de("claude", "toolu_A")
        self.assertEqual(ident, hashlib.sha256("claude\0toolu_A".encode("utf-8")).hexdigest()[:10])
        self.assertEqual(reg.id_de("claude", "toolu_A"), ident)
        self.assertRegex(ident, RE_ID)
        self.assertNotEqual(reg.id_de("codex", "toolu_A"), ident)
        self.assertEqual(reg.diretorio_dados({"PAINEL_IAS_DIR": str(self.base / "x"),
                                              "LOCALAPPDATA": str(self.base / "y")}), self.base / "x")
        self.assertEqual(reg.diretorio_dados({"LOCALAPPDATA": str(self.base / "local")}),
                         self.base / "local" / "painel-ias")
        self.assertEqual(reg.diretorio_dados({"PAINEL_IAS_DIR": "", "LOCALAPPDATA": str(self.base / "local")}),
                         self.base / "local" / "painel-ias")
        with patch.object(reg.Path, "home", return_value=self.base / "casa"):
            self.assertEqual(reg.diretorio_dados({}), self.base / "casa" / "painel-ias")
        with patch.dict(os.environ, {"PAINEL_IAS_DIR": str(self.base / "amb")}):
            self.assertEqual(reg.diretorio_dados(), self.base / "amb")

    # ------------------------------------------------------------ correção depois das revisões, itens 59 a 62

    def test_59_ocorrencia_sem_campo_obrigatorio_ignorada(self):
        self.escrever(self.arq, {"type": "system"})
        self.marcar()
        self.escrever(self.arq, falha("toolu_A"), falha("toolu_B"))
        self.passada()
        ida, idb = reg.id_de("claude", "toolu_A"), reg.id_de("claude", "toolu_B")
        alvo = self.dados / reg.ARQ_OCORRENCIAS
        original = json.loads(alvo.read_bytes())
        for chave in ("id", "agente", "ferramenta", "classe", "codigo", "hora", "arquivo", "offset", "chave"):
            with self.subTest(chave=chave):
                editado = json.loads(json.dumps(original))
                del editado["ocorrencias"][ida][chave]
                alvo.write_text(json.dumps(editado), encoding="utf-8")
                self.assertEqual(set(reg.carregar_ocorrencias(self.dados)["ocorrencias"]), {idb})
                self.assertEqual([i["id"] for i in reg.listar(self.dados, T + 200)], [idb])
                resumo = reg.resumo_publico(reg.carregar_ocorrencias(self.dados), {}, T + 200)
                self.assertEqual([i["id"] for i in resumo["itens"]], [idb])
                estado, erro = reg.varrer(self.dados, T + 200, self.claude, self.codex)
                self.assertIsNone(erro)
                self.assertEqual(set(estado["ocorrencias"]), {idb})
        controle = json.loads(json.dumps(original))
        controle["ocorrencias"][ida]["codigo"] = None
        alvo.write_text(json.dumps(controle), encoding="utf-8")
        carregado = reg.carregar_ocorrencias(self.dados)["ocorrencias"]
        self.assertEqual(set(carregado), {ida, idb})
        self.assertIsNone(carregado[ida]["codigo"])

    def test_60_registro_fora_do_formato_ignorado(self):
        ida, idb, _idc = self.tres_abertos()
        alvo = self.dados / reg.ARQ_REGISTRO
        ev = {"tipo": "teste", "comando": "py -m unittest", "codigo": 0, "arquivo": "erros-evidencias/" + ida + ".txt",
              "sha256": "0" * 64, "bytes": 3}
        verificado = {"estado": "verificado", "corrigido_em": T + 150, "nota": None, "verificado_em": T + 160,
                      "evidencia": ev}
        corrigido = {"estado": "corrigido", "corrigido_em": T + 150, "nota": None, "verificado_em": None,
                     "evidencia": None}

        def sem(d, chave):
            d = dict(d)
            del d[chave]
            return d

        for bom in (verificado, corrigido):     # controle: o formato completo é aceito sem abrir a evidência
            alvo.write_text(json.dumps({"versao": 1, "registros": {ida: bom}}), encoding="utf-8")
            self.assertEqual(reg.carregar_registro(self.dados, False), {ida: bom})
        casos = {"só estado": {"estado": "verificado"},
                 "verificado sem evidencia": sem(verificado, "evidencia"),
                 "evidencia sem sha256": dict(verificado, evidencia=sem(ev, "sha256")),
                 "arquivo de outro id": dict(verificado, evidencia=dict(ev, arquivo="erros-evidencias/" + idb + ".txt")),
                 "bytes 0": dict(verificado, evidencia=dict(ev, bytes=0)),
                 "verificado_em texto": dict(verificado, verificado_em="ontem"),
                 "corrigido sem corrigido_em": sem(corrigido, "corrigido_em"),
                 "nota número": dict(corrigido, nota=7)}
        for nome, registro in casos.items():
            with self.subTest(nome):
                alvo.write_text(json.dumps({"versao": 1, "registros": {ida: registro}}), encoding="utf-8")
                self.assertEqual(reg.carregar_registro(self.dados, True), {})
                self.assertEqual(reg.carregar_registro(self.dados, False), {})
                (item,) = [i for i in reg.listar(self.dados, T + 200) if i["id"] == ida]
                self.assertEqual(item["estado"], "aberto")
                resumo = reg.resumo_publico(reg.carregar_ocorrencias(self.dados),
                                            reg.carregar_registro(self.dados, True), T + 200)
                self.assertEqual(resumo["contagens"]["verificado"], 0)
                reg.registrar_corrigido(self.dados, ida, None, T + 200)
                self.assertEqual(reg.carregar_registro(self.dados, False)[ida]["estado"], "corrigido")

    @unittest.skipUnless(reg.zstd is not None, "sem compression.zstd")
    def test_61_zst_sem_quebra_final_nao_consumido(self):
        self.marcar()
        linha = jl(evento(item_codex("call-q")))
        self.zst.parent.mkdir(parents=True)
        self.zst.write_bytes(reg.zstd.compress(linha[:-1]))
        os.utime(self.zst, (MTIME, MTIME))
        self.assertEqual(self.passada()["ocorrencias"], {})
        entrada = self.disco()["arquivos"][str(self.zst)]
        self.assertEqual(entrada["offset"], 0)
        self.assertIs(entrada["fim"], True)
        self.zst.write_bytes(reg.zstd.compress(linha))
        os.utime(self.zst, (MTIME + 1, MTIME + 1))
        estado = self.passada(T + 150)
        self.assertEqual(len(estado["ocorrencias"]), 1)
        self.assertEqual(set(self.por_chave(estado)), {"thread-1:call-q"})
        with patch.object(reg.zstd, "open", wraps=reg.zstd.open) as espia:
            estado = self.passada(T + 200)
        self.assertEqual(espia.call_count, 0)
        self.assertEqual(len(estado["ocorrencias"]), 1)

    @unittest.skipUnless(reg.zstd is not None, "sem compression.zstd")
    def test_62_teto_max_zst_retoma_na_passada_seguinte(self):
        self.marcar()
        primeira = jl(evento(item_codex("call-1")))
        segunda = jl(evento(item_codex("call-2")))
        self.zst.parent.mkdir(parents=True)
        self.zst.write_bytes(reg.zstd.compress(primeira + segunda))
        os.utime(self.zst, (MTIME, MTIME))
        with patch.object(reg, "MAX_ZST", len(primeira)):
            estado = self.passada()
            self.assertEqual(len(estado["ocorrencias"]), 1)
            self.assertIs(self.disco()["arquivos"][str(self.zst)]["fim"], False)
            passadas = 1
            while not self.disco()["arquivos"][str(self.zst)]["fim"]:
                self.assertLess(passadas, 3)
                estado = self.passada(T + 100 + 10 * passadas)
                passadas += 1
            self.assertEqual(len(estado["ocorrencias"]), 2)
            self.assertEqual(set(self.por_chave(estado)), {"thread-1:call-1", "thread-1:call-2"})
            self.assertEqual(self.disco()["arquivos"][str(self.zst)]["offset"], len(primeira + segunda))
            with patch.object(reg.zstd, "open", wraps=reg.zstd.open) as espia:
                estado = self.passada(T + 200)
            self.assertEqual(espia.call_count, 0)
            self.assertEqual(len(estado["ocorrencias"]), 2)


if __name__ == "__main__":
    unittest.main()
