"""Presença no WSL: checagem antes da consulta, tabela fixa, agregação e coletor.

Nenhum teste chama o wsl.exe real: o executor vem em ctx["executar"] (falso, que registra cada
chamada) ou o subprocess.run do módulo é trocado por um falso via patch.
"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch, Mock

HOME = "/home/fulano"
FORA = 987001  # pai não reconhecido (ex.: shell)
CLAUDE = HOME + "/.local/share/claude/versions/2.1.314"
CODEX_BIN = HOME + "/.local/bin/codex"
CODEX_NVM = (HOME + "/.nvm/versions/node/v24.21.0/lib/node_modules/@openai/codex/node_modules/"
             "@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex")
GROK_DOWN = HOME + "/.grok/downloads/grok-1.0.62-linux-x86_64"
GROK_NVM = HOME + "/.nvm/versions/node/v24.21.0/lib/node_modules/@xai-official/grok/bin/grok-native"
CURSOR = HOME + "/.local/share/cursor-agent/versions/2026.10.01-e373342/node"
SCRIPT_ESPERADO = r'''printf 'H\t%s\n' "$HOME"
for p in /proc/[0-9]*; do
  e=$(readlink "$p/exe" 2>/dev/null) || continue
  s=$(cat "$p/stat" 2>/dev/null) || continue
  s=${s##*) }
  set -- $s
  printf 'P\t%s\t%s\t%s\t%s\n' "${p#/proc/}" "$2" "${20}" "$e"
done
printf 'F\n'
'''


def utf16(texto):
    """Listagem como o wsl.exe a entrega: UTF-16LE sem BOM."""
    return texto.encode("utf-16-le")


def p(pid, exe, ppid=FORA, inicio=1000):
    return (pid, ppid, inicio, exe)


def saida(processos, home=HOME, fim=True):
    """Resposta no formato do SCRIPT: linha H, linhas P (pid, pai, início, exe) e F."""
    linhas = ["H\t%s" % home] + ["P\t%s\t%s\t%s\t%s" % processo for processo in processos]
    if fim:
        linhas.append("F")
    return ("\n".join(linhas) + "\n").encode("utf-8")


class Executor:
    """Executor falso: registra (argv, timeout); devolve a listagem na checagem e `consulta` no -d."""

    def __init__(self, listagem=utf16("Ubuntu-26.04\r\n"), consulta=b"H\t/home/fulano\nF\n"):
        self.listagem, self.consulta = listagem, consulta
        self.chamadas = []

    def __call__(self, argv, timeout):
        self.chamadas.append((list(argv), timeout))
        resposta = self.consulta if "-d" in argv else self.listagem
        if isinstance(resposta, BaseException):
            raise resposta
        return resposta


class PresencaLinuxTests(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("lib.fontes.presenca_linux")
        # Garantia extra: se algum caminho fugir do executor falso, nada real é disparado.
        guarda = patch.object(self.mod.subprocess, "run", side_effect=AssertionError("subprocess real"))
        guarda.start()
        self.addCleanup(guarda.stop)

    def ler(self, executor, **extra):
        return self.mod.ler(dict({"executar": executor}, **extra))

    @staticmethod
    def com_d(executor):
        return [argv for argv, _ in executor.chamadas if "-d" in argv]

    def contar(self, processos):
        executor = Executor(consulta=saida(processos))
        resultado = self.ler(executor)
        self.assertEqual(resultado["presenca_linux"]["estado"], "ok")
        self.assertEqual(len(executor.chamadas), 2)
        return {item["produto"]: item["instancias"] for item in resultado["presenca_linux"]["itens"]}

    # 1
    def test_01_distro_parada_so_checa(self):
        executor = Executor(listagem=utf16("Outra\r\n"))
        resultado = self.ler(executor)
        self.assertEqual(resultado, {"presenca_linux": {"estado": "parada", "host": "Ubuntu-26.04", "itens": []}})
        self.assertEqual(executor.chamadas, [(self.mod.ARGV_CHECAGEM, 2)])
        self.assertEqual(executor.chamadas[0][0], ["wsl.exe", "--list", "--running", "--quiet"])
        self.assertEqual(self.com_d(executor), [])

    # 2
    def test_02_lista_vazia_e_parada(self):
        executor = Executor(listagem=b"")
        resultado = self.ler(executor)
        self.assertEqual(resultado, {"presenca_linux": {"estado": "parada", "host": "Ubuntu-26.04", "itens": []}})
        self.assertEqual(len(executor.chamadas), 1)
        self.assertEqual(self.com_d(executor), [])

    # 3
    def test_03_checagem_com_falha_fecha_sem_consulta(self):
        casos = (OSError("x"), subprocess.TimeoutExpired(["wsl.exe"], 2), RuntimeError("segredo"),
                 "Ubuntu-26.04\n")
        for falha in casos:
            with self.subTest(falha=repr(falha)):
                executor = Executor(listagem=falha)
                resultado = self.ler(executor)
                self.assertEqual(resultado, {"erro": self.mod.ERRO_CHECAGEM})
                self.assertNotIn("segredo", json.dumps(resultado, ensure_ascii=False))
                self.assertEqual(len(executor.chamadas), 1)
                self.assertEqual(self.com_d(executor), [])

    # 4
    def test_04_codificacoes_da_listagem(self):
        casos = {"utf16_sem_bom": utf16("Ubuntu-26.04\r\n"),
                 "utf16_com_bom": b"\xff\xfe" + utf16("Ubuntu-26.04\r\n"),
                 "utf8_puro": b"Ubuntu-26.04\n",
                 "utf8_com_bom": "﻿Ubuntu-26.04\n".encode("utf-8"),
                 "crlf_varias": b"kali-linux\r\nUbuntu-26.04\r\n",
                 "outra_caixa": utf16("ubuntu-26.04\r\n"),
                 "utf16_varias": utf16("kali-linux\r\nUbuntu-26.04\r\n")}
        for nome, listagem in casos.items():
            with self.subTest(caso=nome):
                executor = Executor(listagem=listagem)
                resultado = self.ler(executor)
                self.assertEqual(resultado["presenca_linux"]["estado"], "ok")
                self.assertEqual(len(executor.chamadas), 2)
                self.assertEqual(len(self.com_d(executor)), 1)

    # 5
    def test_05_nome_parecido_nao_vale(self):
        for nome in ("Ubuntu", "Ubuntu-26.04-dev", "xUbuntu-26.04"):
            with self.subTest(nome=nome):
                executor = Executor(listagem=utf16(nome + "\r\n"))
                resultado = self.ler(executor)
                self.assertEqual(resultado["presenca_linux"]["estado"], "parada")
                self.assertEqual(len(executor.chamadas), 1)
                self.assertEqual(self.com_d(executor), [])

    # 6
    def test_06_ordem_e_forma_das_chamadas(self):
        self.assertEqual(self.mod.DISTRO, "Ubuntu-26.04")
        self.assertEqual((self.mod.T_CHECAGEM_S, self.mod.T_CONSULTA_S), (2, 5))
        executor = Executor(consulta=saida([]))
        self.ler(executor)
        self.assertEqual(executor.chamadas, [
            (["wsl.exe", "--list", "--running", "--quiet"], 2),
            (["wsl.exe", "-d", "Ubuntu-26.04", "-e", "sh", "-c", self.mod.SCRIPT], 5)])
        self.assertIs(executor.chamadas[1][0][-1], self.mod.SCRIPT)

    # 7
    def test_07_consulta_com_falha(self):
        for falha in (OSError("x"), subprocess.TimeoutExpired(["wsl.exe"], 5)):
            with self.subTest(falha=repr(falha)):
                executor = Executor(consulta=falha)
                self.assertEqual(self.ler(executor), {"erro": self.mod.ERRO_CONSULTA})
                self.assertEqual(len(executor.chamadas), 2)

    # 8
    def test_08_resposta_fora_do_formato(self):
        casos = {"truncada": saida([p(5, CLAUDE)], fim=False),
                 "f_fora_do_fim": b"H\t/home/fulano\nF\nP\t5\t1\t9\t/x\n",
                 "sem_h": b"P\t5\t1\t9\t/x\nF\n",
                 "duas_h": b"H\t/home/fulano\nH\t/home/outro\nF\n",
                 "h_relativa": b"H\thome/fulano\nF\n",
                 "h_com_asterisco": b"H\t/home/*\nF\n",
                 "vazia": b""}
        for nome, resposta in casos.items():
            with self.subTest(caso=nome):
                self.assertEqual(self.ler(Executor(consulta=resposta)), {"erro": self.mod.ERRO_FORMATO})

    # 9
    def test_09_classificacao_um_produto_por_caminho_real(self):
        processos = [p(11, CLAUDE), p(12, CODEX_BIN), p(13, CODEX_NVM), p(14, GROK_DOWN), p(15, GROK_NVM),
                     p(16, CURSOR)]
        self.assertEqual(self.contar(processos),
                         {"claude_cli": 1, "codex_cli": 2, "grok_cli": 2, "cursor_agent": 1})
        tabela = self.mod.tabela_resolvida(HOME)
        for caminho, produto in ((CLAUDE, "claude_cli"), (CODEX_BIN, "codex_cli"), (CODEX_NVM, "codex_cli"),
                                 (GROK_DOWN, "grok_cli"), (GROK_NVM, "grok_cli"), (CURSOR, "cursor_agent")):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.mod.classificar(caminho, tabela), produto)

    # 10
    def test_10_executaveis_que_nao_contam(self):
        caminhos = (HOME + "/.nvm/versions/node/v24.21.0/bin/node", "/usr/bin/python3.14", "/usr/bin/dash",
                    "/usr/bin/sleep", "/tmp/claude", "/home/outro/.local/bin/codex",
                    HOME + "/.local/share/claude/versions/a/b", HOME + "/.local/share/cursor-agent/versions/x/rg")
        self.assertEqual(self.contar([p(100 + i, c) for i, c in enumerate(caminhos)]), {})
        tabela = self.mod.tabela_resolvida(HOME)
        for caminho in caminhos:
            with self.subTest(caminho=caminho):
                self.assertIsNone(self.mod.classificar(caminho, tabela))

    # 11
    def test_11_maiusculas_diferenciam(self):
        self.assertEqual(self.contar([p(7, HOME + "/.LOCAL/bin/codex")]), {})
        tabela = self.mod.tabela_resolvida(HOME)
        self.assertIsNone(self.mod.classificar(HOME + "/.LOCAL/bin/codex", tabela))
        self.assertIsNone(self.mod.classificar(HOME + "/.local/bin/Codex", tabela))
        self.assertEqual(self.mod.classificar(CODEX_BIN, tabela), "codex_cli")

    # 12
    def test_12_sufixo_deleted(self):
        self.assertEqual(self.contar([p(8, HOME + "/.local/share/claude/versions/2.1.308 (deleted)")]),
                         {"claude_cli": 1})
        tabela = self.mod.tabela_resolvida(HOME)
        # Sem curinga no último segmento, só a remoção do sufixo exato faz casar.
        self.assertEqual(self.mod.classificar(CODEX_BIN + " (deleted)", tabela), "codex_cli")
        self.assertIsNone(self.mod.classificar(CODEX_BIN + "(deleted)", tabela))

    # 13
    def test_13_pai_e_filho(self):
        casos = {"mesmo_produto_pai_mais_velho": ([p(10, CLAUDE, FORA, 100), p(11, CLAUDE, 10, 200)],
                                                  {"claude_cli": 1}),
                 "mesmo_produto_mesmo_inicio": ([p(10, CLAUDE, FORA, 100), p(11, CLAUDE, 10, 100)],
                                                {"claude_cli": 1}),
                 "pai_de_outro_produto": ([p(20, CODEX_BIN, FORA, 100), p(21, CLAUDE, 20, 200)],
                                          {"codex_cli": 1, "claude_cli": 1}),
                 "ppid_igual_ao_pid": ([p(30, CLAUDE, 30, 100)], {"claude_cli": 1}),
                 "pid_reutilizado": ([p(40, CLAUDE, FORA, 500), p(41, CLAUDE, 40, 300)], {"claude_cli": 2})}
        for nome, (processos, esperado) in casos.items():
            with self.subTest(caso=nome):
                self.assertEqual(self.contar(processos), esperado)

    # 14
    def test_14_linhas_p_malformadas_sao_ignoradas(self):
        resposta = ("H\t/home/fulano\n"
                    "P\t1\t2\t" + CODEX_BIN + "\n"
                    "P\tabc\t2\t3\t" + CODEX_BIN + "\n"
                    "P\t5\tx\t3\t" + CODEX_BIN + "\n"
                    "P\t6\t2\t-3\t" + CODEX_BIN + "\n"
                    "P\t7\t2\t3\thome/fulano/.local/bin/codex\n"
                    "P\t8\t2\t3\t\n"
                    "P\t9\t2\t3\t" + CLAUDE + "\n"
                    "F\n").encode("utf-8")
        executor = Executor(consulta=resposta)
        resultado = self.ler(executor)
        self.assertEqual(resultado["presenca_linux"]["estado"], "ok")
        self.assertEqual({i["produto"]: i["instancias"] for i in resultado["presenca_linux"]["itens"]},
                         {"claude_cli": 1})
        self.assertEqual(self.mod.interpretar(resposta),
                         (HOME, [{"pid": 9, "ppid": 2, "inicio": 3, "exe": CLAUDE}]))

    # 15
    def test_15_itens_na_ordem_da_tabela(self):
        processos = [p(1, CURSOR), p(2, GROK_DOWN), p(3, CODEX_BIN), p(4, CLAUDE)]
        resultado = self.ler(Executor(consulta=saida(processos)))
        self.assertEqual([i["produto"] for i in resultado["presenca_linux"]["itens"]],
                         ["claude_cli", "codex_cli", "grok_cli", "cursor_agent"])
        self.assertEqual([linha["produto"] for linha in self.mod.PRODUTOS],
                         ["claude_cli", "codex_cli", "grok_cli", "cursor_agent"])

    # 16
    def test_16_privacidade_so_constantes_nome_e_inteiros(self):
        processos = [p(987654, CLAUDE, 987001, 876543), p(987655, CODEX_NVM, 987654, 876544),
                     p(987656, GROK_NVM, 987002, 876545), p(987657, CURSOR, 987003, 876546),
                     p(987658, HOME + "/.nvm/versions/node/v24.21.0/bin/node", 987004, 876547)]
        resultado = self.ler(Executor(consulta=saida(processos)))
        texto = json.dumps(resultado, ensure_ascii=False)
        for proibido in ("fulano", "/home", "/", ".local", "nvm", "v24"):
            self.assertNotIn(proibido, texto)
        for pid, ppid, inicio, _ in processos:
            for numero in (pid, ppid, inicio):
                self.assertNotIn(str(numero), texto)
        self.assertEqual(set(resultado), {"presenca_linux"})
        self.assertEqual(set(resultado["presenca_linux"]), {"estado", "host", "itens"})
        constantes = {(l["produto"], l["rotulo"], l["familia"], l["tipo"]) for l in self.mod.PRODUTOS}
        self.assertEqual(len(resultado["presenca_linux"]["itens"]), 4)
        for item in resultado["presenca_linux"]["itens"]:
            self.assertEqual(set(item), {"produto", "rotulo", "familia", "tipo", "instancias"})
            self.assertIn((item["produto"], item["rotulo"], item["familia"], item["tipo"]), constantes)
            self.assertIs(type(item["instancias"]), int)

    # 17
    def test_17_nome_de_distro_invalido_nao_executa_nada(self):
        for nome in ("a b", "x;rm", "", "a" * 65, "nome$(x)", "a\nb", "-e", "--help", "-", None, 123):
            with self.subTest(nome=repr(nome)):
                executor = Executor()
                self.assertEqual(self.ler(executor, distro=nome), {"erro": self.mod.ERRO_DISTRO})
                self.assertEqual(executor.chamadas, [])
        # Limite do tamanho: 64 caracteres ainda valem (e só a checagem roda, pois não está ligada).
        executor = Executor()
        self.assertEqual(self.ler(executor, distro="a" * 64)["presenca_linux"]["estado"], "parada")
        self.assertEqual(len(executor.chamadas), 1)

    # 18
    def test_18_kali_nunca_e_consultada(self):
        executor = Executor(listagem=utf16("Ubuntu-26.04\r\n"))
        resultado = self.ler(executor, distro="kali-linux")
        self.assertEqual(resultado, {"presenca_linux": {"estado": "parada", "host": "kali-linux", "itens": []}})
        self.assertEqual(len(executor.chamadas), 1)
        self.assertEqual(self.com_d(executor), [])

    # 19
    def test_19_sem_executor_fora_do_windows(self):
        with patch.object(self.mod.os, "name", "posix"):
            self.assertEqual(self.mod.ler({}), {"erro": self.mod.ERRO_PLATAFORMA})
        self.mod.subprocess.run.assert_not_called()

    # 20
    def test_20_rodar_real_com_subprocess_falso(self):
        def concluido(codigo, stdout):
            return subprocess.CompletedProcess(["wsl.exe"], codigo, stdout=stdout, stderr=b"")

        with patch.object(self.mod.subprocess, "run", return_value=concluido(0, b"Ubuntu-26.04")) as falso:
            self.assertEqual(self.mod._rodar({}, ["wsl.exe", "--list"], 2), b"Ubuntu-26.04")
        argumentos, opcoes = falso.call_args
        self.assertEqual(argumentos, (["wsl.exe", "--list"],))
        self.assertIs(opcoes["capture_output"], True)
        self.assertEqual(opcoes["timeout"], 2)
        self.assertNotIn("shell", opcoes)
        self.assertNotIn("text", opcoes)
        if os.name == "nt":
            self.assertNotEqual(opcoes.get("creationflags", 0), 0)
        with patch.object(self.mod.subprocess, "run", return_value=concluido(1, b"")):
            with self.assertRaises(OSError):
                self.mod._rodar({}, list(self.mod.ARGV_CHECAGEM), 2)
        # Nenhuma distro ligada (D14): saída vazia com código 0 volta como b"".
        with patch.object(self.mod.subprocess, "run", return_value=concluido(0, b"")):
            vazio = self.mod._rodar({}, list(self.mod.ARGV_CHECAGEM), 2)
        self.assertEqual(vazio, b"")
        self.assertIsInstance(vazio, bytes)
        if os.name == "nt":
            # ler sem executor: código != 0 na checagem fecha sem consulta; vazio com 0 é parada.
            with patch.object(self.mod.subprocess, "run", return_value=concluido(1, b"")) as falso:
                self.assertEqual(self.mod.ler({}), {"erro": self.mod.ERRO_CHECAGEM})
            self.assertEqual(falso.call_count, 1)
            self.assertEqual(falso.call_args[0][0], self.mod.ARGV_CHECAGEM)
            with patch.object(self.mod.subprocess, "run", return_value=concluido(0, b"")) as falso:
                self.assertEqual(self.mod.ler({})["presenca_linux"]["estado"], "parada")
            self.assertEqual(falso.call_count, 1)

    # 21
    def test_21_script_literal_e_so_le_exe_e_stat(self):
        self.assertEqual(self.mod.SCRIPT.split("\n"), SCRIPT_ESPERADO.split("\n"))
        self.assertEqual(self.mod.SCRIPT, SCRIPT_ESPERADO)
        self.assertIn('"${20}"', self.mod.SCRIPT)
        for proibido in ("cmdline", "environ", "/cwd"):
            self.assertNotIn(proibido, self.mod.SCRIPT)

    # 26 (correção)
    def test_26_distro_do_ctx_vai_para_a_linha_de_comando(self):
        executor = Executor(listagem=utf16("Debian-12\r\nUbuntu-26.04\r\n"))
        resultado = self.ler(executor, distro="Debian-12")
        self.assertEqual(len(executor.chamadas), 2)
        self.assertEqual(executor.chamadas[1],
                         (["wsl.exe", "-d", "Debian-12", "-e", "sh", "-c", self.mod.SCRIPT], 5))
        self.assertEqual(resultado, {"presenca_linux": {"estado": "ok", "host": "Debian-12", "itens": []}})


class IntegracaoPresencaLinuxTests(unittest.TestCase):
    VARIAVEIS = ("PAINEL_PRESENCA_LINUX", "PAINEL_PRESENCA", "PAINEL_GASTOS")

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_presenca_linux', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.dado = {'estado': 'ok', 'host': 'Ubuntu-26.04',
                     'itens': [{'produto': 'claude_cli', 'rotulo': 'Claude Code CLI', 'familia': 'claude',
                                'tipo': 'cli', 'instancias': 1}]}
        self.fontes = {'whatsapp': Mock(return_value={'whatsapp': {'estado': 'connected'}}),
                       'gastos_hermes': Mock(return_value={'gastos_hermes': {'fixture': True}}),
                       'presenca_processos': Mock(return_value={'presenca_processos': {
                           'estado': 'ok', 'itens': [], 'ilegiveis': 0}}),
                       'presenca_linux': Mock(return_value={'presenca_linux': self.dado})}
        for nome, valor in [('gravar', None), ('montar', {'agentes': []})]:
            guarda = (patch.object(self.coletor, nome, side_effect=lambda *a: {'agentes': []})
                      if nome == 'montar' else patch.object(self.coletor, nome, return_value=valor))
            guarda.start()
            self.addCleanup(guarda.stop)
        # O resultado não pode depender do ambiente de quem roda os testes.
        ambiente = patch.dict(os.environ)
        ambiente.start()
        self.addCleanup(ambiente.stop)
        for variavel in self.VARIAVEIS:
            os.environ.pop(variavel, None)

    def ligar(self, **valores):
        for variavel in self.VARIAVEIS:
            os.environ.pop(variavel, None)
        os.environ.update(valores)

    def passada(self, hora):
        return self.coletor.passada_viva(self.args, fontes=self.fontes,
                                        ctx={'agora': hora}, cache=self.cache)

    # 22
    def test_22_opt_in_desligado_nao_registra_nao_chama_nao_publica(self):
        for valor in (None, '0', 'true'):
            with self.subTest(valor=valor), patch.dict(os.environ):
                self.ligar(**({} if valor is None else {'PAINEL_PRESENCA_LINUX': valor}))
                self.cache.clear()
                self.assertNotIn('presenca_linux', self.coletor.fontes_vivas(True))
                self.assertNotIn('presenca_linux', self.coletor.extras_ativos())
                d = self.passada(1000)
                self.assertNotIn('presenca_linux', d)
                self.assertNotIn('presenca_linux', d['fontes'])
                self.fontes['presenca_linux'].assert_not_called()

    # 23
    def test_23_opt_in_ligado_registra_publica_e_respeita_cadencia(self):
        self.ligar(PAINEL_PRESENCA_LINUX='1')
        registradas = self.coletor.fontes_vivas(True)
        self.assertIn('presenca_linux', registradas)
        self.assertEqual(registradas['presenca_linux'].__name__, 'ler')
        self.assertEqual(registradas['presenca_linux'].__module__, 'fontes.presenca_linux')
        d = self.passada(1000)
        self.assertEqual(d['presenca_linux'], self.dado)
        self.assertIs(d['fontes']['presenca_linux']['ok'], True)
        self.assertEqual(set(d['fontes']['presenca_linux']),
                         {'ok', 'erro', 'hora', 'ultima_leitura_ok', 'desatualizado'})
        fonte = self.fontes['presenca_linux']
        self.assertEqual(fonte.call_count, 1)
        self.passada(1010)
        self.assertEqual(fonte.call_count, 1)
        self.passada(1021)
        self.assertEqual(fonte.call_count, 2)

    # 24
    def test_24_falha_retem_ate_300s_e_depois_vira_erro(self):
        self.ligar(PAINEL_PRESENCA_LINUX='1')
        self.passada(1000)
        self.fontes['presenca_linux'].return_value = {'erro': 'presença Linux: consulta à distro falhou'}
        d = self.passada(1020)
        self.assertEqual(d['presenca_linux'], self.dado)
        meta = d['fontes']['presenca_linux']
        self.assertIs(meta['desatualizado'], True)
        self.assertIs(meta['ok'], False)
        self.assertEqual(meta['ultima_leitura_ok'], 1000)
        d = self.passada(1300)
        self.assertEqual(d['presenca_linux'], self.dado)
        self.assertIs(d['fontes']['presenca_linux']['desatualizado'], True)
        d = self.passada(1301)
        self.assertEqual(d['presenca_linux'], {'estado': 'erro'})
        self.assertIs(d['fontes']['presenca_linux']['desatualizado'], False)
        self.assertIs(d['fontes']['presenca_linux']['ok'], False)

    # 25
    def test_25_variaveis_independentes_e_ordem_das_extras(self):
        casos = (({'PAINEL_PRESENCA_LINUX': '1'}, ('presenca_linux',)),
                 ({'PAINEL_PRESENCA_LINUX': '1', 'PAINEL_PRESENCA': '1', 'PAINEL_GASTOS': '1'},
                  ('whatsapp', 'gastos_hermes', 'presenca_processos', 'presenca_linux')))
        todas = ('whatsapp', 'gastos_hermes', 'presenca_processos', 'presenca_linux')
        for valores, esperadas in casos:
            with self.subTest(valores=valores), patch.dict(os.environ):
                self.ligar(**valores)
                self.cache.clear()
                self.assertEqual(self.coletor.extras_ativos(), esperadas)
                registradas = self.coletor.fontes_vivas(True)
                d = self.passada(1000)
                for chave in todas:
                    if chave in esperadas:
                        self.assertIn(chave, registradas)
                        self.assertIn(chave, d)
                        self.assertIn(chave, d['fontes'])
                    else:
                        self.assertNotIn(chave, registradas)
                        self.assertNotIn(chave, d)
                        self.assertNotIn(chave, d['fontes'])
                self.assertEqual(tuple(k for k in d if k in todas), esperadas)


if __name__ == "__main__":
    unittest.main()
