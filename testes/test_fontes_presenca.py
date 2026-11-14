"""Presença por processo: tabela fixa, agregação e coletor, com listador falso (sem processo real).

Só o teste de fumaça (item 18) consulta o kernel de verdade, e só lê a própria entrada.
"""
import importlib
import importlib.util
import json
import ntpath
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock

HOME = r"C:\Users\Fulano"
LOCAL = r"C:\Users\Fulano\AppData\Local"
PF = r"C:\Program Files"
AMBIENTE = {"USERPROFILE": HOME, "LOCALAPPDATA": LOCAL, "ProgramW6432": PF}
FORA = 4321  # pai não reconhecido (ex.: terminal)


def proc(pid, caminho, ppid=FORA, criado=1000, nome=None):
    return {"pid": pid, "ppid": ppid, "nome": nome or (ntpath.basename(caminho) if caminho else "x.exe"),
            "caminho": caminho, "criado": criado}


class PresencaTests(unittest.TestCase):
    def fonte(self):
        self.assertIsNotNone(importlib.util.find_spec('lib.fontes.presenca_processos'))
        return importlib.import_module('lib.fontes.presenca_processos')

    def ler(self, processos, ambiente=AMBIENTE):
        """Listador falso: devolve tudo (sem pré-filtro) para a classificação ser testada de verdade."""
        self.predicados = []

        def listar(predicado):
            self.predicados.append(predicado)
            return list(processos)

        return self.fonte().ler({"ambiente": dict(ambiente), "listar_processos": listar})

    def itens(self, processos, ambiente=AMBIENTE):
        saida = self.ler(processos, ambiente)
        self.assertEqual(set(saida), {"presenca_processos"})
        self.assertEqual(saida["presenca_processos"]["estado"], "ok")
        return saida["presenca_processos"]["itens"]

    def contagem(self, processos, ambiente=AMBIENTE):
        return {i["produto"]: i["instancias"] for i in self.itens(processos, ambiente)}

    # 1
    def test_01_wrapper_e_filho_do_mesmo_produto_contam_um(self):
        lista = [proc(987654, LOCAL + r"\hermes\bin\hermes.exe", criado=1000),
                 proc(987655, LOCAL + r"\hermes\hermes-agent\venv\Scripts\hermes.exe",
                      ppid=987654, criado=1001),
                 proc(987656, r"C:\Python314\python.exe", ppid=987655, criado=1002)]
        self.assertEqual(self.contagem(lista), {"hermes_cli": 1})

    # 2
    def test_02_electron_raiz_mais_oito_filhos_e_neto_conta_um(self):
        exe = LOCAL + r"\Programs\Grok Bot\Grok Bot.exe"
        lista = [proc(987700, exe, criado=5000)]
        lista += [proc(987701 + i, exe, ppid=987700, criado=5001 + i) for i in range(8)]
        lista.append(proc(987709, exe, ppid=987701, criado=5010))
        self.assertEqual(self.contagem(lista), {"grok_bot": 1})

    # 3
    def test_03_node_e_python_genericos_nao_contam(self):
        lista = [proc(987654, r"C:\Program Files\nodejs\node.exe"),
                 proc(987655, r"C:\Python314\python.exe"),
                 proc(987656, LOCAL + r"\Programs\Python\Python314\python.exe")]
        self.assertEqual(self.itens(lista), [])

    # 4
    def test_04_cursor_agent_so_com_um_segmento_de_versao(self):
        self.assertEqual(self.contagem([proc(987654, LOCAL + r"\cursor-agent\versions\2026.10.14-941492e\node.exe")]),
                         {"cursor_agent": 1})
        self.assertEqual(self.itens([proc(987655, LOCAL + r"\cursor-agent\versions\a\b\node.exe")]), [])

    # 5
    def test_05_nome_conhecido_em_caminho_estranho_nao_conta(self):
        lista = [proc(987654, r"C:\Temp\claude.exe"), proc(987655, r"C:\Temp\agent.exe"),
                 proc(987656, r"C:\Temp\github.exe"),
                 proc(987657, LOCAL + r"\Programs\Maestri\resources\cli\maestri.exe")]
        self.assertEqual(self.itens(lista), [])

    # 6
    def test_06_pid_reutilizado_nao_vira_pai(self):
        exe = HOME + r"\.local\bin\claude.exe"
        pai_mais_novo = [proc(987654, exe, criado=9000), proc(987655, exe, ppid=987654, criado=8000)]
        self.assertEqual(self.contagem(pai_mais_novo), {"claude_cli": 2})
        for criado_filho in (9000, 9500):
            with self.subTest(criado_filho=criado_filho):
                lista = [proc(987654, exe, criado=9000), proc(987655, exe, ppid=987654, criado=criado_filho)]
                self.assertEqual(self.contagem(lista), {"claude_cli": 1})

    # 7
    def test_07_duas_clis_independentes_contam_dois(self):
        exe = HOME + r"\.local\bin\claude.exe"
        lista = [proc(987600, r"C:\Program Files\WindowsApps\Terminal\WindowsTerminal.exe", ppid=1),
                 proc(987654, exe, ppid=987600, criado=2000),
                 proc(987655, exe, ppid=FORA, criado=1000)]
        self.assertEqual(self.contagem(lista), {"claude_cli": 2})

    # 8
    def test_08_executavel_renomeado_pela_autoatualizacao(self):
        lista = [proc(987654, HOME + r"\.local\bin\claude.exe.old.1794690331162.109120")]
        self.assertEqual(self.contagem(lista), {"claude_cli": 1})

    # 9
    def test_09_pacote_versionado_exige_o_editor_certo(self):
        certo = r"C:\Program Files\WindowsApps\Claude_5.11492.5.0_x64__pzs8sxrjxfjjc\app\Claude.exe"
        outro = r"C:\Program Files\WindowsApps\Claude_5.11492.5.0_x64__xxxxxxxxxxxxx\app\Claude.exe"
        self.assertEqual(self.contagem([proc(987654, certo)]), {"claude_app": 1})
        self.assertEqual(self.itens([proc(987655, outro)]), [])

    def test_09b_program_files_cai_para_programfiles_sem_programw6432(self):
        ambiente = {"USERPROFILE": HOME, "LOCALAPPDATA": LOCAL, "ProgramFiles": PF}
        certo = PF + r"\WindowsApps\OpenAI.Codex_26.1.0.0_x64__2p2nqsd0c76g0\app\codex.exe"
        self.assertEqual(self.contagem([proc(987654, certo)], ambiente), {"codex_app": 1})

    # 10
    def test_10_acesso_negado_nao_conta_e_vira_ilegivel(self):
        saida = self.ler([proc(987654, None, criado=None, nome="claude.exe")])["presenca_processos"]
        self.assertEqual(saida["itens"], [])
        self.assertEqual(saida["ilegiveis"], 1)
        saida = self.ler([proc(987655, HOME + r"\.local\bin\claude.exe", criado=None)])["presenca_processos"]
        self.assertEqual(saida["itens"], [])
        self.assertEqual(saida["ilegiveis"], 1)

    # 11
    def test_11_normalizacao_maiusculas_e_barra(self):
        caminho = "C:/USERS/FULANO/.LOCAL/BIN/CLAUDE.EXE"
        self.assertEqual(self.contagem([proc(987654, caminho)]), {"claude_cli": 1})
        mod = self.fonte()
        tabela = mod.tabela_resolvida(AMBIENTE)
        self.assertEqual(mod.classificar("c:/users/fulano/appdata/local/PROGRAMS/cursor/CURSOR.EXE", tabela),
                         "cursor_app")
        self.assertIsNone(mod.classificar(None, tabela))
        self.assertIsNone(mod.classificar("", tabela))

    def test_11b_so_asterisco_e_curinga_e_nunca_atravessa_barra(self):
        mod = self.fonte()
        tabela = mod.tabela_resolvida(AMBIENTE)
        self.assertIsNone(mod.classificar(
            PF + r"\WindowsApps\Claude_1\x__pzs8sxrjxfjjc\app\claude.exe", tabela))
        for estranho in ("F?lano", "F[u]lano"):
            with self.subTest(estranho=estranho):
                ambiente = {"USERPROFILE": r"C:\Users\%s" % estranho, "LOCALAPPDATA": LOCAL}
                tabela = mod.tabela_resolvida(ambiente)
                self.assertIsNone(mod.classificar(r"C:\Users\Fulano\.local\bin\claude.exe", tabela))
                self.assertIsNone(mod.classificar(r"C:\Users\Fxlano\.local\bin\claude.exe", tabela))
                self.assertEqual(mod.classificar(r"C:\Users\%s\.local\bin\claude.exe" % estranho, tabela),
                                 "claude_cli")

    # 12
    def test_12_privacidade_so_constantes_e_inteiros(self):
        mod = self.fonte()
        criados = [133750000000000001 + i for i in range(6)]
        lista = [proc(987654, HOME + r"\.local\bin\claude.exe", criado=criados[0]),
                 proc(987655, HOME + r"\.local\bin\claude.exe", ppid=987654, criado=criados[1]),
                 proc(987656, LOCAL + r"\Programs\Maestri\Maestri.exe", criado=criados[2]),
                 proc(987657, LOCAL + r"\Programs\cursor\Cursor.exe", criado=criados[3]),
                 proc(987658, HOME + r"\.grok\bin\agent.exe", criado=criados[4]),
                 proc(987659, None, criado=None, nome="codex.exe")]
        saida = self.ler(lista)
        texto = json.dumps(saida, ensure_ascii=False)
        for proibido in ("Fulano", "\\", "AppData", ".exe", ".EXE"):
            self.assertNotIn(proibido, texto)
        for item in lista:
            self.assertNotIn(str(item["pid"]), texto)
        for criado in criados:
            self.assertNotIn(str(criado), texto)
        dado = saida["presenca_processos"]
        self.assertEqual(set(dado), {"estado", "itens", "ilegiveis"})
        self.assertIs(type(dado["ilegiveis"]), int)
        self.assertEqual(dado["ilegiveis"], 1)
        self.assertEqual(len(dado["itens"]), 4)
        linhas = {p["produto"]: p for p in mod.PRODUTOS}
        for item in dado["itens"]:
            self.assertEqual(set(item), {"produto", "rotulo", "familia", "tipo", "instancias"})
            self.assertIn(item["produto"], linhas)
            linha = linhas[item["produto"]]
            self.assertEqual(item["rotulo"], linha["rotulo"])
            self.assertEqual(item["familia"], linha["familia"])
            self.assertEqual(item["tipo"], linha["tipo"])
            self.assertIn(item["familia"], mod.FAMILIAS)
            self.assertIn(item["tipo"], mod.TIPOS)
            self.assertIs(type(item["instancias"]), int)
            self.assertGreaterEqual(item["instancias"], 1)

    def test_12b_forma_da_tabela_fixa(self):
        mod = self.fonte()
        self.assertIsInstance(mod.PRODUTOS, tuple)
        self.assertEqual(len(mod.PRODUTOS), 13)
        for linha in mod.PRODUTOS:
            self.assertEqual(set(linha), {"produto", "rotulo", "familia", "tipo", "padroes"})
            self.assertIsInstance(linha["padroes"], tuple)
            self.assertIn(linha["familia"], mod.FAMILIAS)
            self.assertIn(linha["tipo"], mod.TIPOS)
        self.assertEqual(mod.FAMILIAS, ("claude", "codex", "grok", "hermes", "cursor", "copilot", "maestri"))
        self.assertEqual(mod.TIPOS, ("cli", "app"))
        resolvida = mod.tabela_resolvida(AMBIENTE)
        self.assertEqual([l["produto"] for l in resolvida], [l["produto"] for l in mod.PRODUTOS])
        for linha in resolvida:
            self.assertTrue(linha["padroes"])
            for padrao in linha["padroes"]:
                self.assertEqual(padrao, padrao.lower())
                self.assertNotIn("{", padrao)
                self.assertNotIn("/", padrao)

    # 13
    def test_13_sem_gemini(self):
        mod = self.fonte()
        for linha in mod.PRODUTOS:
            textos = [linha["produto"], linha["rotulo"], linha["familia"], *linha["padroes"]]
            for texto in textos:
                self.assertNotIn("gemini", texto.lower())
        self.assertNotIn("gemini", mod.FAMILIAS)

    # 14
    def test_14_itens_saem_na_ordem_da_tabela(self):
        mod = self.fonte()
        lista = [proc(987660, LOCAL + r"\Programs\Maestri\Maestri.exe"),
                 proc(987661, LOCAL + r"\Programs\cursor\Cursor.exe"),
                 proc(987662, LOCAL + r"\GitHubCopilotCLI\copilot.exe"),
                 proc(987663, HOME + r"\.grok\bin\grok.exe"),
                 proc(987664, LOCAL + r"\Programs\OpenAI\Codex\bin\codex.exe"),
                 proc(987665, HOME + r"\.local\bin\claude.exe")]
        esperados = {"maestri", "cursor_app", "copilot_cli", "grok_cli", "codex_cli", "claude_cli"}
        ordem = [p["produto"] for p in mod.PRODUTOS if p["produto"] in esperados]
        for entrada in (lista, list(reversed(lista)), lista[2:] + lista[:2]):
            with self.subTest(primeiro=entrada[0]["pid"]):
                self.assertEqual([i["produto"] for i in self.itens(entrada)], ordem)

    # 15
    def test_15_erros_com_texto_constante(self):
        mod = self.fonte()

        def quebra(_predicado):
            raise OSError("segredo")

        saida = mod.ler({"ambiente": dict(AMBIENTE), "listar_processos": quebra})
        self.assertEqual(saida, {"erro": "presença: falha ao listar processos"})
        self.assertNotIn("segredo", json.dumps(saida, ensure_ascii=False))
        with patch.object(mod.os, "name", "posix"):
            saida = mod.ler({"ambiente": dict(AMBIENTE)})
        self.assertEqual(saida, {"erro": "presença: plataforma sem suporte"})

    def test_15b_outras_excecoes_propagam(self):
        mod = self.fonte()

        def quebra(_predicado):
            raise RuntimeError("x")

        with self.assertRaises(RuntimeError):
            mod.ler({"ambiente": dict(AMBIENTE), "listar_processos": quebra})

    # 16
    def test_16_prefiltro_por_nome(self):
        self.ler([])
        self.assertEqual(len(self.predicados), 1)
        predicado = self.predicados[0]
        for nome in ("claude.exe", "Grok Bot.exe", "node.exe", "claude.exe.old.123.45"):
            with self.subTest(nome=nome):
                self.assertTrue(predicado(nome))
        self.assertFalse(predicado("explorer.exe"))
        self.assertFalse(predicado(""))

    # 17
    def test_17_variavel_ausente_tira_os_padroes_e_nao_levanta(self):
        mod = self.fonte()
        ambiente = {"USERPROFILE": HOME, "ProgramW6432": PF}
        cursor = LOCAL + r"\Programs\cursor\Cursor.exe"
        self.assertEqual(self.itens([proc(987654, cursor)], ambiente), [])
        tabela = {l["produto"]: l for l in mod.tabela_resolvida(ambiente)}
        self.assertEqual(tabela["cursor_app"]["padroes"], ())
        self.assertEqual(len(tabela["claude_cli"]["padroes"]), 2)
        self.assertEqual(len(tabela), len(mod.PRODUTOS))
        # Ambiente vazio injetado não cai no ambiente real.
        self.assertTrue(all(l["padroes"] == () for l in mod.tabela_resolvida({})))
        self.assertEqual(self.itens([proc(987655, HOME + r"\.local\bin\claude.exe")], {}), [])
        self.assertEqual(self.itens([proc(987656, cursor)], dict(AMBIENTE, LOCALAPPDATA="")), [])

    # 18
    @unittest.skipUnless(os.name == "nt", "fumaça real só no Windows")
    def test_18_fumaca_real_le_o_proprio_processo(self):
        mod = self.fonte()
        nome_proprio = ntpath.basename(sys.executable).lower()

        def predicado(nome):
            return nome.lower() == nome_proprio

        lista = mod.listar_windows(predicado)
        # O listador real só devolve (e só abre) quem passa no pré-filtro.
        for lida in lista:
            self.assertTrue(predicado(lida["nome"]))
        self.assertEqual(mod.listar_windows(lambda nome: False), [])
        proprio = [e for e in lista if e["pid"] == os.getpid()]
        self.assertEqual(len(proprio), 1)
        entrada = proprio[0]
        self.assertEqual(set(entrada), {"pid", "ppid", "nome", "caminho", "criado"})
        self.assertIs(type(entrada["ppid"]), int)
        self.assertGreater(entrada["ppid"], 0)
        self.assertIs(type(entrada["criado"]), int)
        self.assertGreater(entrada["criado"], 0)
        self.assertTrue(ntpath.basename(entrada["caminho"]).lower().startswith("python"))

    # 24
    def test_24_codex_cli_pelo_caminho_resolvido_da_junction(self):
        pacote = HOME + r"\.codex\packages\standalone"
        validos = (pacote + r"\releases\0.166.0-x86_64-pc-windows-msvc\bin\codex.exe",
                   LOCAL + r"\Programs\OpenAI\Codex\bin\codex.exe")
        for caminho in validos:
            with self.subTest(caminho=caminho):
                self.assertEqual(self.contagem([proc(987654, caminho)]), {"codex_cli": 1})
        for caminho in (pacote + r"\releases\a\b\bin\codex.exe", pacote + r"\current\bin\codex.exe"):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.itens([proc(987655, caminho)]), [])

    # D4: ppid igual ao próprio pid é raiz
    def test_25_ppid_igual_ao_proprio_pid_e_raiz(self):
        lista = [proc(987654, HOME + r"\.local\bin\claude.exe", ppid=987654, criado=1000)]
        self.assertEqual(self.contagem(lista), {"claude_cli": 1})

    # D4: pai reconhecido de OUTRO produto não dobra o filho
    def test_26_pai_de_outro_produto_nao_dobra(self):
        lista = [proc(987700, LOCAL + r"\Programs\Grok Bot\Grok Bot.exe", criado=100),
                 proc(987701, HOME + r"\.local\bin\claude.exe", ppid=987700, criado=200)]
        self.assertEqual(self.contagem(lista), {"claude_cli": 1, "grok_bot": 1})


class IntegracaoPresencaTests(unittest.TestCase):
    VARIAVEIS = ("PAINEL_PRESENCA", "PAINEL_GASTOS")

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_presenca', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.dado = {'estado': 'ok', 'itens': [{'produto': 'claude_cli', 'rotulo': 'Claude Code CLI',
                                                'familia': 'claude', 'tipo': 'cli', 'instancias': 2}],
                     'ilegiveis': 0}
        self.fontes = {'whatsapp': Mock(return_value={'whatsapp': {'estado': 'connected'}}),
                       'gastos_hermes': Mock(return_value={'gastos_hermes': {'fixture': True}}),
                       'presenca_processos': Mock(return_value={'presenca_processos': self.dado})}
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

    # 19
    def test_19_opt_in_desligado_nao_registra_nao_chama_nao_publica(self):
        for valor in (None, '0', 'true'):
            with self.subTest(valor=valor), patch.dict(os.environ):
                self.ligar(**({} if valor is None else {'PAINEL_PRESENCA': valor}))
                self.cache.clear()
                self.assertNotIn('presenca_processos', self.coletor.fontes_vivas(True))
                self.assertNotIn('presenca_processos', self.coletor.extras_ativos())
                d = self.passada(1000)
                self.assertNotIn('presenca_processos', d)
                self.assertNotIn('presenca_processos', d['fontes'])
                self.fontes['presenca_processos'].assert_not_called()

    # 20
    def test_20_opt_in_ligado_registra_publica_e_respeita_cadencia(self):
        self.ligar(PAINEL_PRESENCA='1')
        registradas = self.coletor.fontes_vivas(True)
        self.assertIn('presenca_processos', registradas)
        self.assertEqual(registradas['presenca_processos'].__name__, 'ler')
        self.assertEqual(registradas['presenca_processos'].__module__, 'fontes.presenca_processos')
        d = self.passada(1000)
        self.assertEqual(d['presenca_processos'], self.dado)
        self.assertIs(d['fontes']['presenca_processos']['ok'], True)
        self.assertEqual(set(d['fontes']['presenca_processos']),
                         {'ok', 'erro', 'hora', 'ultima_leitura_ok', 'desatualizado'})
        fonte = self.fontes['presenca_processos']
        self.assertEqual(fonte.call_count, 1)
        self.passada(1002)
        self.assertEqual(fonte.call_count, 1)
        self.passada(1006)
        self.assertEqual(fonte.call_count, 2)

    # 21
    def test_21_falha_retem_ate_300s_e_depois_vira_erro(self):
        self.ligar(PAINEL_PRESENCA='1')
        self.passada(1000)
        self.fontes['presenca_processos'].return_value = {'erro': 'presença: falha ao listar processos'}
        d = self.passada(1010)
        self.assertEqual(d['presenca_processos'], self.dado)
        meta = d['fontes']['presenca_processos']
        self.assertIs(meta['desatualizado'], True)
        self.assertIs(meta['ok'], False)
        self.assertEqual(meta['ultima_leitura_ok'], 1000)
        d = self.passada(1300)
        self.assertEqual(d['presenca_processos'], self.dado)
        self.assertIs(d['fontes']['presenca_processos']['desatualizado'], True)
        d = self.passada(1301)
        self.assertEqual(d['presenca_processos'], {'estado': 'erro'})
        self.assertIs(d['fontes']['presenca_processos']['desatualizado'], False)
        self.assertIs(d['fontes']['presenca_processos']['ok'], False)

    # 22
    def test_22_desligar_ignora_cache_existente(self):
        self.ligar(PAINEL_PRESENCA='1')
        self.passada(1000)
        self.assertIn('presenca_processos', self.cache)
        with patch.dict(os.environ):
            os.environ.pop('PAINEL_PRESENCA', None)
            d = self.passada(1001)
        self.assertNotIn('presenca_processos', d)
        self.assertNotIn('presenca_processos', d['fontes'])
        self.fontes['presenca_processos'].assert_called_once()

    # 23
    def test_23_variaveis_independentes_e_ordem_das_extras(self):
        casos = (({'PAINEL_PRESENCA': '1'}, ('presenca_processos',)),
                 ({'PAINEL_GASTOS': '1'}, ('whatsapp', 'gastos_hermes')),
                 ({'PAINEL_PRESENCA': '1', 'PAINEL_GASTOS': '1'},
                  ('whatsapp', 'gastos_hermes', 'presenca_processos')))
        todas = ('whatsapp', 'gastos_hermes', 'presenca_processos')
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
