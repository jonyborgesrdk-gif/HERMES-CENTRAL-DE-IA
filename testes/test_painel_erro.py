# -*- coding: utf-8 -*-
"""Testes do comando painel-erro (E20, §10, itens 48 a 57).

Rodar:  python3 -m unittest testes.test_painel_erro -v      (na pasta do painel)

O comando roda como processo filho, com PAINEL_IAS_DIR e LOCALAPPDATA numa pasta temporária e a pasta de
trabalho também temporária: nada aqui toca o diretório de dados de verdade nem os transcripts de verdade.
O estado é montado chamando reg.varrer sobre transcripts sintéticos, na mesma pasta temporária.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(RAIZ / "lib"))

import erros_registro as reg    # noqa: E402

SEGREDO = "sk-" + "a" * 40
OCULTO = "[oculto]"
PASTA = "C:/projetos/demo"
SESSAO = "sessao-demo-1"
THREAD = "thread-demo-1"
SEM_OCORRENCIAS = "nenhuma ocorrência registrada (a captura liga com PAINEL_ERROS=1 no coletor)"
SEM_TEXTO = "painel-erro: texto indisponível: o transcript mudou ou foi apagado"
CORTE = " … (cortado)"
CABECALHO = ["ID", "IDADE", "AGENTE", "FERRAMENTA", "CLASSE", "CÓDIGO", "ESTADO"]
CHAVES = {"id", "agente", "ferramenta", "classe", "codigo", "hora", "estado", "evidencia", "corrigido_em",
          "verificado_em", "nota"}
ERRO_BASH = "Exit code 1\nFAILED: " + SEGREDO + "\n" + "detalhe " * 60
# O segredo começa no caractere 190: cortar em 200 antes de mascarar deixaria "sk-" e 7 letras na saída.
PREFIXO_190 = ("File does not exist. " + "x" * 200)[:189] + " "
ERRO_CORTE = PREFIXO_190 + SEGREDO + " fim do erro"


def uso(tid, nome, entrada, hora):
    return {"type": "assistant", "timestamp": hora,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": nome, "input": entrada}]}}


def resultado(tid, texto, hora, **extra):
    linha = {"type": "user", "uuid": "u-" + tid, "timestamp": hora, "cwd": PASTA, "sessionId": SESSAO,
             "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": True,
                                      "content": texto}]}}
    linha.update(extra)
    return linha


def falha_codex(item_id, comando, saida, codigo, hora):
    return {"type": "event_msg", "timestamp": hora,
            "payload": {"type": "item_completed", "thread_id": THREAD, "completed_at_ms": hora * 1000,
                        "item": {"id": item_id, "type": "CommandExecution", "status": "failed",
                                 "exit_code": codigo, "command": comando, "aggregated_output": saida,
                                 "cwd": PASTA}}}


def aborto_codex(turn_id, hora):
    return {"type": "event_msg", "timestamp": hora,
            "payload": {"type": "turn_aborted", "turn_id": turn_id, "reason": "interrupted", "thread_id": THREAD}}


def anexar(caminho, linhas):
    with open(caminho, "ab") as f:
        for linha in linhas:
            f.write(json.dumps(linha, ensure_ascii=False).encode("utf-8") + b"\n")


def retrato(pasta, fora=()):
    """(caminho relativo, tamanho, sha256) de cada arquivo e (caminho, "pasta", "") de cada pasta."""
    pasta = Path(pasta)
    itens = []
    for p in sorted(pasta.rglob("*")):
        rel = p.relative_to(pasta)
        if rel.parts[0] in fora:
            continue
        if p.is_dir():
            itens.append((rel.as_posix(), "pasta", ""))
        else:
            dados = p.read_bytes()
            itens.append((rel.as_posix(), len(dados), hashlib.sha256(dados).hexdigest()))
    return itens


def texto(bruto):
    return bruto.decode("utf-8").replace("\r\n", "\n")


def tabela(saida):
    return [re.split(r"\s{2,}", linha) for linha in saida.strip().splitlines()]


def ids_listados(saida):
    linhas = tabela(saida)
    assert linhas[0] == CABECALHO, linhas[0]
    return {linha[0] for linha in linhas[1:]}


class PainelErro(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self._tmp.name)
        self.dados = self.raiz / "dados"
        self.claude = self.raiz / "claude-projects"
        self.codex = self.raiz / "codex-sessions"
        self.casa = self.raiz / "casa"            # LOCALAPPDATA dos filhos: nada pode cair aqui
        self.trabalho = self.raiz / "trabalho"    # pasta de trabalho dos filhos: idem
        for pasta in (self.claude / "proj-demo", self.codex / "2026" / "11" / "15", self.casa, self.trabalho):
            pasta.mkdir(parents=True)
        self.arq_claude = self.claude / "proj-demo" / "sessao-demo-1.jsonl"
        self.arq_codex = self.codex / "2026" / "11" / "15" / "rollout-demo-1.jsonl"
        self.t0 = time.time() - 120
        self.hora = self.t0 + 30
        self.ambiente = dict(os.environ, PAINEL_IAS_DIR=str(self.dados), LOCALAPPDATA=str(self.casa))

    def tearDown(self):
        self._tmp.cleanup()

    def comando(self, *args):
        return [sys.executable, str(RAIZ / "painel-erro")] + list(args)

    def rodar(self, *args, ambiente=None):
        p = subprocess.run(self.comando(*args), env=ambiente or self.ambiente, cwd=str(self.trabalho),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return p.returncode, texto(p.stdout), texto(p.stderr)

    def montar(self, claude, codex):
        """Primeira passada só marca a posição; os erros chegam depois e a segunda passada os captura."""
        anexar(self.arq_claude, [{"type": "user", "uuid": "u-0", "timestamp": self.t0,
                                  "message": {"content": [{"type": "text", "text": "começo"}]}}])
        anexar(self.arq_codex, [{"type": "session_meta", "timestamp": self.t0, "payload": {"id": THREAD}}])
        estado, erro = reg.varrer(self.dados, self.t0, self.claude, self.codex)
        self.assertIsNone(erro)
        self.assertEqual(estado["ocorrencias"], {})
        anexar(self.arq_claude, claude)
        anexar(self.arq_codex, codex)
        estado, erro = reg.varrer(self.dados, self.t0 + 61, self.claude, self.codex)
        self.assertIsNone(erro)
        return estado

    def cenario(self):
        h = self.hora
        self.montar(
            [uso("toolu_bash", "Bash", {"command": "py -m unittest " + SEGREDO}, h),
             resultado("toolu_bash", ERRO_BASH, h),
             uso("toolu_neg", "Bash", {"command": "git push"}, h),
             resultado("toolu_neg", "Permission to use Bash has been denied.", h, toolDenialKind="rule"),
             uso("toolu_corte", "Read", {"file_path": PASTA + "/leia.txt"}, h),
             resultado("toolu_corte", ERRO_CORTE, h)],
            [falha_codex("item-1", ["git", "status"], "fatal: not a git repository", 128, h),
             aborto_codex("turn-1", h)])
        ids = {"bash": reg.id_de("claude", "toolu_bash"), "negado": reg.id_de("claude", "toolu_neg"),
               "corte": reg.id_de("claude", "toolu_corte"), "codex": reg.id_de("codex", THREAD + ":item-1"),
               "aborto": reg.id_de("codex", "abort:turn-1")}
        self.assertEqual(set(reg.carregar_ocorrencias(self.dados)["ocorrencias"]), set(ids.values()))
        self.assertNotIn("0123456789", ids.values())
        return ids

    # 48
    def test_48_ajuda_e_sem_argumentos(self):
        for opcao in ("--help", "-h", "--ajuda"):
            codigo, out, err = self.rodar(opcao)
            self.assertEqual((codigo, err), (0, ""), opcao)
            self.assertIn("painel-erro listar", out)
            self.assertIn("painel-erro verificado ID --tipo", out)
        codigo, out, err = self.rodar()
        self.assertEqual(codigo, 2)
        self.assertTrue(err.startswith("painel-erro: "), err)
        self.assertNotIn("Traceback", err)

    # 49
    def test_49_listar_sem_arquivo_de_estado(self):
        codigo, out, err = self.rodar("listar")
        self.assertEqual((codigo, out.strip(), err), (0, SEM_OCORRENCIAS, ""))
        self.assertFalse(self.dados.exists())

    # 50
    def test_50_listar_filtros_e_json(self):
        ids = self.cenario()
        codigo, out, err = self.rodar("listar")
        self.assertEqual((codigo, err), (0, ""))
        self.assertEqual(ids_listados(out), {ids["bash"], ids["corte"], ids["codex"]})
        por_id = {linha[0]: linha for linha in tabela(out)[1:]}
        self.assertEqual(por_id[ids["bash"]][2:], ["claude", "bash", "codigo_saida", "1", "aberto"])
        self.assertEqual(por_id[ids["corte"]][2:], ["claude", "read", "arquivo", "-", "aberto"])
        self.assertEqual(por_id[ids["codex"]][2:], ["codex", "comando", "codigo_saida", "128", "aberto"])
        for linha in por_id.values():
            self.assertEqual(len(linha), 7, linha)
            self.assertRegex(linha[1], r"^\d+ (s|min|h|d)$")

        codigo, out, err = self.rodar("listar", "--estado", "todos")
        self.assertEqual((codigo, err), (0, ""))
        self.assertEqual(ids_listados(out), set(ids.values()))
        por_id = {linha[0]: linha for linha in tabela(out)[1:]}
        self.assertEqual(por_id[ids["negado"]][2:], ["claude", "bash", "permissao_negada", "-", "contado"])
        self.assertEqual(por_id[ids["aborto"]][2:], ["codex", "nenhuma", "interrompido", "-", "contado"])

        codigo, out, err = self.rodar("listar", "--agente", "codex")
        self.assertEqual((codigo, ids_listados(out)), (0, {ids["codex"]}))
        codigo, out, err = self.rodar("listar", "--agente", "codex", "--estado", "todos")
        self.assertEqual((codigo, ids_listados(out)), (0, {ids["codex"], ids["aborto"]}))
        codigo, out, err = self.rodar("listar", "--estado", "todos", "--limite", "2")
        self.assertEqual((codigo, len(ids_listados(out))), (0, 2))
        codigo, out, err = self.rodar("listar", "--estado", "verificado")
        self.assertEqual((codigo, out.strip()), (0, "nenhuma ocorrência com estado verificado"))

        codigo, out, err = self.rodar("listar", "--estado", "todos", "--json")
        self.assertEqual((codigo, err), (0, ""))
        lista = json.loads(out)
        self.assertEqual({i["id"] for i in lista}, set(ids.values()))
        for item in lista:
            self.assertEqual(set(item), CHAVES)
        self.assertEqual({i["id"]: i["estado"] for i in lista},
                         {ids["bash"]: "aberto", ids["corte"]: "aberto", ids["codex"]: "aberto",
                          ids["negado"]: "contado", ids["aborto"]: "contado"})
        self.assertNotIn("sk-", out)

    # 51
    def test_51_ver_mascara_corta_e_nao_grava(self):
        ids = self.cenario()
        # Escape de terminal, campainha e marca de direção de texto no erro: cada um sai trocado por U+FFFD.
        erro_controle = "Exit code 2\n\x1b[31mvermelho\x07 fim \u202eao contrário"
        anexar(self.arq_claude, [uso("toolu_ctrl", "Bash", {"command": "make"}, self.hora),
                                 resultado("toolu_ctrl", erro_controle, self.hora)])
        self.assertIsNone(reg.varrer(self.dados, self.t0 + 62, self.claude, self.codex)[1])
        ids["controle"] = reg.id_de("claude", "toolu_ctrl")
        ocorrencias = reg.carregar_ocorrencias(self.dados)["ocorrencias"]
        self.assertIn(ids["controle"], ocorrencias)
        antes = retrato(self.dados)

        codigo, out, err = self.rodar("ver", ids["bash"])
        self.assertEqual((codigo, err), (0, ""))
        self.assertIn("id: %s\n" % ids["bash"], out)
        self.assertIn("agente: claude | ferramenta: bash | classe: codigo_saida | código: 1 | estado: aberto\n", out)
        self.assertIn("transcript: %s\n" % ocorrencias[ids["bash"]]["arquivo"], out)
        self.assertIn("sessão: %s\n" % SESSAO, out)
        self.assertIn("pasta: %s\n" % PASTA, out)
        self.assertIn("--- entrada da ferramenta ---\n"
                      + json.dumps({"command": "py -m unittest " + OCULTO}, ensure_ascii=False) + "\n", out)
        self.assertIn("--- erro ---\n" + ERRO_BASH.replace(SEGREDO, OCULTO) + "\n", out)
        self.assertNotIn("sk-", out)
        self.assertNotIn(CORTE, out)

        codigo, out, err = self.rodar("ver", ids["bash"], "--bytes", "200")
        self.assertEqual((codigo, err), (0, ""))
        self.assertIn("--- erro ---\n" + ERRO_BASH.replace(SEGREDO, OCULTO)[:200] + CORTE + "\n", out)
        self.assertNotIn("sk-", out)

        self.assertEqual(ERRO_CORTE.index("sk-"), 190)
        codigo, out, err = self.rodar("ver", ids["corte"], "--bytes", "200")
        self.assertEqual((codigo, err), (0, ""))
        self.assertNotIn("sk-", out)
        self.assertIn("--- erro ---\n" + ERRO_CORTE.replace(SEGREDO, OCULTO)[:200] + CORTE + "\n", out)

        codigo, out, err = self.rodar("ver", ids["codex"])
        self.assertEqual((codigo, err), (0, ""))
        self.assertIn("agente: codex | ferramenta: comando | classe: codigo_saida | código: 128 | estado: aberto\n",
                      out)
        self.assertIn("sessão: %s\npasta: %s\n" % (THREAD, PASTA), out)
        self.assertIn("--- entrada da ferramenta ---\ngit status\n--- erro ---\nfatal: not a git repository\n", out)

        codigo, out, err = self.rodar("ver", ids["controle"])
        self.assertEqual((codigo, err), (0, ""))
        for caractere in ("\x1b", "\x07", "\u202e"):
            self.assertNotIn(caractere, out)
        self.assertIn("--- erro ---\nExit code 2\n\ufffd[31mvermelho\ufffd fim \ufffdao contrário\n", out)

        for args in (("listar",), ("listar", "--estado", "todos", "--json")):
            self.assertEqual(self.rodar(*args)[0], 0)
        self.assertEqual(retrato(self.dados), antes)

        os.remove(self.arq_codex)
        codigo, out, err = self.rodar("ver", ids["codex"])
        self.assertEqual((codigo, err.strip()), (1, SEM_TEXTO))
        self.assertEqual(retrato(self.dados), antes)

        # Estado editado à mão: caminho com \0 e offset absurdo dão texto indisponível, não rastro de pilha.
        arq_estado = self.dados / reg.ARQ_OCORRENCIAS
        original = json.loads(arq_estado.read_bytes())
        arquivo = original["ocorrencias"][ids["bash"]]["arquivo"]
        for campo, valor in (("arquivo", arquivo + "\0"), ("offset", 2 ** 70)):
            editado = json.loads(json.dumps(original))
            editado["ocorrencias"][ids["bash"]][campo] = valor
            arq_estado.write_text(json.dumps(editado), encoding="utf-8")
            codigo, out, err = self.rodar("ver", ids["bash"])
            self.assertEqual((codigo, err.strip()), (1, SEM_TEXTO), campo)
            self.assertNotIn("Traceback", err)
            self.assertNotIn("\0", out)

    # 52
    def test_52_corrigido(self):
        ids = self.cenario()
        codigo, out, err = self.rodar("corrigido", ids["bash"], "--nota", "rodei de novo e passou")
        self.assertEqual((codigo, out.strip(), err), (0, "registrado: %s corrigido" % ids["bash"], ""))
        codigo, out, err = self.rodar("listar", "--estado", "corrigido")
        self.assertEqual((codigo, ids_listados(out)), (0, {ids["bash"]}))
        self.assertEqual(reg.carregar_registro(self.dados, False)[ids["bash"]]["nota"], "rodei de novo e passou")

        for ident, mensagem in (("0123ABCDEF", "id inválido"), ("../0123abc", "id inválido"),
                                ("0123456789", "id desconhecido"), (ids["bash"], "já está corrigido")):
            codigo, out, err = self.rodar("corrigido", ident)
            self.assertEqual((codigo, err.strip()), (2, "painel-erro: " + mensagem), ident)

        ausente = self.raiz / "nao-existe"
        codigo, out, err = self.rodar("corrigido", "0123456789",
                                      ambiente=dict(self.ambiente, PAINEL_IAS_DIR=str(ausente)))
        self.assertEqual((codigo, err.strip()), (2, "painel-erro: id desconhecido"))
        self.assertNotIn("Traceback", err)
        self.assertFalse(ausente.exists())

        codigo, out, err = self.rodar("listar", "-h")
        self.assertEqual(codigo, 2)
        self.assertTrue(err.startswith("painel-erro: argumentos inválidos"), err)

    # 53
    def test_53_verificado(self):
        ids = self.cenario()
        ident = ids["codex"]
        saida = self.raiz / "saida-do-teste.txt"
        saida.write_bytes(b"Ran 3 tests\nOK\n")
        base = ("verificado", ident, "--tipo", "teste", "--comando", "py -m unittest", "--codigo", "0")

        codigo, out, err = self.rodar(*base)
        self.assertEqual(codigo, 2)
        self.assertTrue(err.startswith("painel-erro: argumentos inválidos"), err)
        codigo, out, err = self.rodar(*base, "--saida", str(self.raiz / "nao-existe.txt"))
        self.assertEqual((codigo, err.strip()), (2, "painel-erro: --saida: arquivo ausente"))
        self.assertFalse((self.dados / reg.DIR_EVIDENCIAS).exists())

        codigo, out, err = self.rodar(*base, "--saida", str(saida))
        self.assertEqual((codigo, out.strip(), err),
                         (0, "registrado: %s verificado (evidência: teste, código 0)" % ident, ""))
        self.assertEqual((self.dados / reg.DIR_EVIDENCIAS / (ident + ".txt")).read_bytes(), saida.read_bytes())
        codigo, out, err = self.rodar("listar", "--estado", "verificado", "--json")
        self.assertEqual((codigo, err), (0, ""))
        lista = json.loads(out)
        self.assertEqual([i["id"] for i in lista], [ident])
        self.assertEqual(lista[0]["evidencia"], {"tipo": "teste", "codigo": 0})

    # 54
    def test_54_permissao_negada_nao_entra_no_fluxo(self):
        ids = self.cenario()
        saida = self.raiz / "saida.txt"
        saida.write_bytes(b"ok\n")
        esperado = ("painel-erro: esta ocorrência só é contada (classe permissao_negada); "
                    "não entra no fluxo de correção")
        codigo, out, err = self.rodar("corrigido", ids["negado"], "--nota", "tentei")
        self.assertEqual((codigo, err.strip()), (2, esperado))
        codigo, out, err = self.rodar("verificado", ids["negado"], "--tipo", "comando", "--comando", "git status",
                                      "--codigo", "0", "--saida", str(saida))
        self.assertEqual((codigo, err.strip()), (2, esperado))
        self.assertFalse((self.dados / reg.ARQ_REGISTRO).exists())
        self.assertFalse((self.dados / reg.DIR_EVIDENCIAS).exists())

    # 55
    def test_55_oito_corrigidos_simultaneos(self):
        claude = []
        for n in range(8):
            claude += [uso("toolu_p%d" % n, "Bash", {"command": "comando %d" % n}, self.hora),
                       resultado("toolu_p%d" % n, "Exit code 2\nfalhou %d" % n, self.hora)]
        self.montar(claude, [])
        ids = [reg.id_de("claude", "toolu_p%d" % n) for n in range(8)]
        filhos, saidas = [], []
        try:
            for n, ident in enumerate(ids):
                filhos.append(subprocess.Popen(self.comando("corrigido", ident, "--nota", "paralelo %d" % n),
                                               env=self.ambiente, cwd=str(self.trabalho), stdin=subprocess.DEVNULL,
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            for filho in filhos:
                out, err = filho.communicate(timeout=60)
                saidas.append((filho.returncode, texto(out), texto(err)))
        finally:
            for filho in filhos:
                if filho.poll() is None:
                    filho.kill()
                filho.wait(timeout=30)
                for fluxo in (filho.stdout, filho.stderr):
                    fluxo.close()
        for (codigo, out, err), ident in zip(saidas, ids):
            self.assertEqual((codigo, out.strip(), err), (0, "registrado: %s corrigido" % ident, ""))
        registros = reg.carregar_registro(self.dados, False)
        self.assertEqual({i: registros[i]["estado"] for i in ids if i in registros}, dict.fromkeys(ids, "corrigido"))
        self.assertFalse((self.dados / reg.ARQ_TRAVA).exists())

    # 56
    def test_56_registro_ilegivel_falha_fechado(self):
        ids = self.cenario()
        arquivo = self.dados / reg.ARQ_REGISTRO
        arquivo.write_bytes(b'{"versao": 1, "registros": ')
        antes = arquivo.read_bytes()
        codigo, out, err = self.rodar("corrigido", ids["bash"])
        self.assertEqual((codigo, err.strip()), (1, "painel-erro: estado local ilegível"))
        codigo, out, err = self.rodar("listar")
        self.assertEqual((codigo, err.strip()), (1, "painel-erro: estado local ilegível"))
        self.assertEqual(arquivo.read_bytes(), antes)
        self.assertFalse((self.dados / reg.ARQ_TRAVA).exists())

    # 57
    def test_57_nada_muda_fora_da_pasta_de_dados(self):
        ids = self.cenario()
        saida = self.raiz / "saida.txt"
        saida.write_bytes(b"ok\n")
        transcripts = (retrato(self.claude), retrato(self.codex))
        fora = retrato(self.raiz, fora=("dados",))
        comandos = [("--help",), (), ("listar",), ("listar", "--estado", "todos", "--json"),
                    ("ver", ids["bash"]), ("ver", ids["codex"], "--bytes", "200"),
                    ("corrigido", ids["bash"], "--nota", "feito"), ("corrigido", ids["negado"]),
                    ("corrigido", "0123ABCDEF"),
                    ("verificado", ids["codex"], "--tipo", "comando", "--comando", "git status", "--codigo", "1",
                     "--saida", str(saida)),
                    ("verificado", ids["corte"], "--tipo", "teste", "--comando", "x", "--codigo", "0"),
                    ("listar", "-h")]
        self.assertEqual([self.rodar(*args)[0] for args in comandos], [0, 2, 0, 0, 0, 0, 0, 2, 2, 0, 2, 2])
        self.assertEqual((retrato(self.claude), retrato(self.codex)), transcripts)
        self.assertEqual(retrato(self.raiz, fora=("dados",)), fora)
        self.assertEqual(list(self.casa.iterdir()), [])
        self.assertEqual(list(self.trabalho.iterdir()), [])

    # 63
    def test_63_estado_editado_a_mao_com_entradas_incompletas(self):
        ids = self.cenario()
        arq_estado = self.dados / reg.ARQ_OCORRENCIAS
        editado = json.loads(arq_estado.read_bytes())
        del editado["ocorrencias"][ids["corte"]]["codigo"]
        arq_estado.write_text(json.dumps(editado), encoding="utf-8")
        arq_registro = self.dados / reg.ARQ_REGISTRO
        arq_registro.write_text(json.dumps({"versao": 1, "registros": {ids["bash"]: {"estado": "verificado"}}}),
                                encoding="utf-8")
        antes = (arq_estado.read_bytes(), arq_registro.read_bytes())
        codigo, out, err = self.rodar("listar", "--json")
        self.assertEqual(codigo, 0, err)
        self.assertNotIn("Traceback", err)
        por_id = {i["id"]: i for i in json.loads(out)}
        self.assertNotIn(ids["corte"], por_id)
        self.assertEqual(por_id[ids["bash"]]["estado"], "aberto")
        codigo, out, err = self.rodar("listar", "--estado", "verificado", "--json")
        self.assertEqual((codigo, json.loads(out)), (0, []), err)
        self.assertNotIn("Traceback", err)
        self.assertEqual((arq_estado.read_bytes(), arq_registro.read_bytes()), antes)

    # N1: Grok e Hermes não guardam texto; ver diz isso e não mostra o caminho do arquivo ou do banco
    def test_65_ver_grok_e_hermes_sem_texto_e_sem_caminho(self):
        constante = "texto não guardado para este agente (só classe e código)"
        self.assertIn("claude|codex|grok|hermes", Path(RAIZ / "painel-erro").read_text(encoding="utf-8").split('"""')[1])
        ocs = {}
        for agente, arquivo in (("grok", "C:/caminho-secreto-grok/events.jsonl"), ("hermes", "C:/caminho-secreto-hermes/state.db")):
            chave = agente + "-chave-1"
            ident = reg.id_de(agente, chave)
            ocs[ident] = {"id": ident, "agente": agente, "ferramenta": "comando", "classe": "outra", "codigo": None,
                          "hora": self.hora, "arquivo": arquivo, "offset": 0, "chave": chave}
        reg._gravar_ocorrencias(self.dados, {"versao": reg.VERSAO, "criado_em": self.t0, "arquivos": {}, "ocorrencias": ocs})
        for ident, oc in ocs.items():
            codigo, out, err = self.rodar("ver", ident)
            self.assertEqual(codigo, 0, err)
            self.assertIn(constante, out)
            self.assertNotIn("transcript:", out)
            self.assertNotIn("caminho-secreto", out + err)
            self.assertIn("agente: " + oc["agente"], out)


if __name__ == "__main__":
    unittest.main()
