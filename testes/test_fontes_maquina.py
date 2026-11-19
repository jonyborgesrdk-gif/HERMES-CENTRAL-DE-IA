"""Fonte da máquina (CPU e discos): fake da camada ctypes, nenhum teste lê a máquina real."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from lib.fontes import maquina as mod

AGORA = 2_000_000_000
GIB = 1024 ** 3


class SistemaFalso:
    """Fake da camada kernel32. unidades: {letra: (tipo, espaco | Exception)}."""

    def __init__(self, tempos, unidades=None):
        self.sequencia = list(tempos)
        self.unidades = unidades if unidades is not None else {"C": (3, (100 * GIB, 500 * GIB))}
        self.falha_mascara = False

    def tempos_cpu(self):
        item = self.sequencia.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def mascara_unidades(self):
        if self.falha_mascara:
            raise OSError("sem unidades")
        return sum(1 << (ord(letra) - 65) for letra in self.unidades)

    def tipo(self, letra):
        return self.unidades[letra[0]][0]

    def espaco(self, letra):
        valor = self.unidades[letra[0]][1]
        if isinstance(valor, Exception):
            raise valor
        return valor


def ler(sistema, estado, agora=AGORA):
    return mod.ler({"maquina_sistema": sistema, "maquina_estado": estado, "agora": agora})


class MaquinaTests(unittest.TestCase):
    def duas(self, primeira, segunda, dt=10, unidades=None):
        sistema, estado = SistemaFalso([primeira, segunda], unidades), {}
        ler(sistema, estado, AGORA)
        return ler(sistema, estado, AGORA + dt)

    def test_01_primeira_passada_cpu_nulo_e_discos_publicados(self):
        r = ler(SistemaFalso([(10, 50, 50)]), {})
        self.assertEqual(list(r), ["maquina"])
        m = r["maquina"]
        self.assertIsNone(m["cpu_pct"])
        self.assertIsNone(m["cpu_janela_s"])
        self.assertEqual(m["lido_em_epoch"], AGORA)
        self.assertEqual(m["discos"], [{"unidade": "C:", "total_bytes": 500 * GIB, "livre_bytes": 100 * GIB, "usado_pct": 80.0}])
        self.assertEqual(m["discos_sem_leitura"], 0)

    def test_02_delta_com_numeros_conhecidos(self):
        # kernel inclui idle: delta kernel 1000 (idle 750), user 500 -> total 1500, ocioso 750 -> 50,0%
        m = self.duas((1000, 5000, 2000), (1750, 6000, 2500))["maquina"]
        self.assertEqual(m["cpu_pct"], 50.0)
        self.assertEqual(m["cpu_janela_s"], 10)
        # 1 casa decimal: ocioso 1 de 3 -> 66,666...% de uso -> 66,7
        m = self.duas((0, 0, 0), (1, 2, 1))["maquina"]
        self.assertEqual(m["cpu_pct"], 66.7)
        # tudo ocioso e tudo ocupado
        self.assertEqual(self.duas((0, 0, 0), (100, 100, 0))["maquina"]["cpu_pct"], 0.0)
        self.assertEqual(self.duas((0, 0, 0), (0, 100, 100))["maquina"]["cpu_pct"], 100.0)

    def test_03_delta_zero_ou_contador_recuou_vira_nulo_sem_dividir_por_zero(self):
        for segundo in ((1000, 5000, 2000), (900, 5000, 2000), (1000, 4000, 2000), (1000, 5000, 1500), (9000, 5100, 2000)):
            with self.subTest(segundo=segundo):
                m = self.duas((1000, 5000, 2000), segundo)["maquina"]
                self.assertIsNone(m["cpu_pct"])
                self.assertIsNone(m["cpu_janela_s"])
        # janela zero ou negativa
        self.assertIsNone(self.duas((0, 0, 0), (50, 100, 100), dt=0)["maquina"]["cpu_pct"])
        self.assertIsNone(self.duas((0, 0, 0), (50, 100, 100), dt=-5)["maquina"]["cpu_pct"])

    def test_04_leitura_depois_do_recuo_vira_a_nova_base(self):
        sistema, estado = SistemaFalso([(1000, 5000, 2000), (10, 50, 20), (60, 150, 70)]), {}
        ler(sistema, estado, AGORA)
        self.assertIsNone(ler(sistema, estado, AGORA + 10)["maquina"]["cpu_pct"])
        self.assertEqual(ler(sistema, estado, AGORA + 20)["maquina"]["cpu_pct"], 66.7)

    def test_05_removivel_rede_cdrom_e_desconhecido_ignorados(self):
        unidades = {"A": (2, (1, 2)), "C": (3, (1 * GIB, 2 * GIB)), "D": (2, (1, 2)), "E": (4, (1, 2)),
                    "F": (5, (1, 2)), "G": (6, (1, 2)), "H": (0, (1, 2)), "I": (1, (1, 2)), "Z": (3, (GIB, GIB))}
        m = ler(SistemaFalso([(1, 2, 3)], unidades), {})["maquina"]
        self.assertEqual([d["unidade"] for d in m["discos"]], ["C:", "Z:"])
        self.assertEqual(m["discos_sem_leitura"], 0)

    def test_06_erro_numa_unidade_omite_e_conta(self):
        unidades = {"C": (3, (GIB, 2 * GIB)), "D": (3, OSError("negado")), "E": (3, (5, 0)), "F": (3, (9, 3)),
                    "G": (3, ("x", 10)), "H": (3, (True, 10))}
        m = ler(SistemaFalso([(1, 2, 3)], unidades), {})["maquina"]
        self.assertEqual([d["unidade"] for d in m["discos"]], ["C:"])
        self.assertEqual(m["discos_sem_leitura"], 5)

    def test_07_erro_geral_e_constante_e_nao_mexe_na_base(self):
        sistema, estado = SistemaFalso([OSError("texto do sistema C:\\segredo")]), {}
        self.assertEqual(ler(sistema, estado), {"erro": mod.ERRO_LER})
        self.assertEqual(estado, {})
        sistema = SistemaFalso([(1, 2, 3)])
        sistema.falha_mascara = True
        self.assertEqual(ler(sistema, {}), {"erro": mod.ERRO_LER})
        self.assertEqual(ler(SistemaFalso([(-1, 2, 3)]), {}), {"erro": mod.ERRO_LER})

    def test_08_fora_do_windows_erro_constante(self):
        with patch.object(mod, "ctypes", Mock(spec=[])):
            self.assertEqual(mod.ler({"agora": AGORA}), {"erro": "só funciona no Windows"})
        self.assertEqual(mod.ERRO_WINDOWS, "só funciona no Windows")

    def test_09_json_sem_texto_do_sistema_alem_da_letra(self):
        unidades = {"C": (3, (GIB, 2 * GIB)), "D": (3, OSError("C:\\Users\\alguem\\segredo"))}
        sistema, estado = SistemaFalso([(0, 0, 0), (50, 100, 100)], unidades), {}
        ler(sistema, estado)
        saida = ler(sistema, estado, AGORA + 10)
        texto = json.dumps(saida)
        self.assertNotIn("segredo", texto)
        self.assertNotIn("alguem", texto)
        m = saida["maquina"]
        self.assertEqual(set(m), {"cpu_pct", "cpu_janela_s", "discos", "discos_sem_leitura", "lido_em_epoch"})
        for disco in m["discos"]:
            self.assertEqual(set(disco), {"unidade", "total_bytes", "livre_bytes", "usado_pct"})
            self.assertRegex(disco["unidade"], r"^[A-Z]:$")
            for chave in ("total_bytes", "livre_bytes"):
                self.assertIsInstance(disco[chave], int)
        self.assertIsInstance(m["cpu_pct"], float)

    def test_10_sem_injecao_usa_estado_do_modulo(self):
        sistema = SistemaFalso([(0, 0, 0), (50, 100, 100)])
        with patch.dict(mod._ESTADO, clear=True):
            self.assertIsNone(mod.ler({"maquina_sistema": sistema, "agora": AGORA})["maquina"]["cpu_pct"])
            self.assertEqual(mod.ler({"maquina_sistema": sistema, "agora": AGORA + 10})["maquina"]["cpu_pct"], 75.0)

    def test_11_modulo_so_stdlib_sem_subprocesso_nem_rede(self):
        import ast
        arvore = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(a.name for a in no.names)
            elif isinstance(no, ast.ImportFrom):
                importados.add(("." * no.level) + (no.module or ""))
        self.assertEqual(importados, {"ctypes", "re", "."})

    @unittest.skipUnless(hasattr(__import__("ctypes"), "windll"), "só no Windows")
    def test_12_sistema_windows_real_so_leitura(self):
        sistema = mod.SistemaWindows()
        idle, kernel, user = sistema.tempos_cpu()
        self.assertTrue(0 <= idle <= kernel)
        self.assertGreater(kernel + user, 0)
        self.assertGreater(sistema.mascara_unidades(), 0)


class IntegracaoMaquinaTests(unittest.TestCase):
    VARIAVEIS = ("PAINEL_MAQUINA", "PAINEL_VIGILANTE", "PAINEL_COTA_CODEX_LOCAL", "PAINEL_COTA_GROK_BOT", "PAINEL_COTA_CURSOR",
                 "PAINEL_PRESENCA_LINUX", "PAINEL_PRESENCA", "PAINEL_GASTOS", "PAINEL_ERROS", "PAINEL_AGENTES_EXTRA", "PAINEL_REDE")

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_maquina', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.dado = {'cpu_pct': None, 'cpu_janela_s': None, 'discos': [], 'discos_sem_leitura': 0, 'lido_em_epoch': 1.0}
        self.fontes = {'maquina': Mock(return_value={'maquina': self.dado})}
        for nome in ('gravar', 'montar'):
            guarda = (patch.object(self.coletor, nome, side_effect=lambda *a: {'agentes': []})
                      if nome == 'montar' else patch.object(self.coletor, nome, return_value=None))
            guarda.start()
            self.addCleanup(guarda.stop)
        ambiente = patch.dict(os.environ)
        ambiente.start()
        self.addCleanup(ambiente.stop)
        self.ligar()

    def ligar(self, **valores):
        for variavel in self.VARIAVEIS:
            os.environ.pop(variavel, None)
        os.environ.update(valores)

    def passada(self, hora):
        return self.coletor.passada_viva(self.args, fontes=self.fontes, ctx={'agora': hora}, cache=self.cache)

    def test_20_desligado_nao_registra_nao_chama_nao_publica(self):
        for valor in (None, '0', 'true', 'sim', ''):
            with self.subTest(valor=valor):
                self.ligar(**({} if valor is None else {'PAINEL_MAQUINA': valor}))
                self.cache.clear()
                self.assertNotIn('maquina', self.coletor.fontes_vivas(True))
                self.assertNotIn('maquina', self.coletor.extras_ativos())
                d = self.passada(1000)
                self.assertNotIn('maquina', d)
                self.assertNotIn('maquina', d['fontes'])
                self.fontes['maquina'].assert_not_called()

    def test_21_ligado_registra_publica_e_respeita_cadencia_de_10s(self):
        self.ligar(PAINEL_MAQUINA='1')
        registradas = self.coletor.fontes_vivas(True)
        self.assertIn('maquina', registradas)
        self.assertEqual(registradas['maquina'].__module__, 'fontes.maquina')
        self.assertEqual(self.coletor.extras_ativos(), ('maquina',))
        d = self.passada(1000)
        self.assertEqual(d['maquina'], self.dado)
        self.assertIs(d['fontes']['maquina']['ok'], True)
        fonte = self.fontes['maquina']
        self.assertEqual(fonte.call_count, 1)
        self.passada(1009)
        self.assertEqual(fonte.call_count, 1)
        self.passada(1011)
        self.assertEqual(fonte.call_count, 2)

    def test_22_erro_retem_ate_300s_e_depois_vira_estado_erro(self):
        self.ligar(PAINEL_MAQUINA='1')
        self.passada(1000)
        self.fontes['maquina'].return_value = {'erro': mod.ERRO_LER}
        d = self.passada(1100)
        self.assertEqual(d['maquina'], self.dado)
        self.assertIs(d['fontes']['maquina']['desatualizado'], True)
        d = self.passada(1301)
        self.assertEqual(d['maquina'], {'estado': 'erro'})


if __name__ == "__main__":
    unittest.main()
