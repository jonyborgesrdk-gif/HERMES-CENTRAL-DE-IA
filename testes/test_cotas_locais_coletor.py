"""Cotas locais no coletor: opt-in, ordem das extras, cadência e retenção.

Fontes injetadas (falsas); nenhum teste lê ~/.codex, %APPDATA% real nem a rede.
"""
import os
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

FONTES = ('cota_codex_local', 'cota_grok_bot', 'cota_cursor')
VARIAVEL = {'cota_codex_local': 'PAINEL_COTA_CODEX_LOCAL',
            'cota_grok_bot': 'PAINEL_COTA_GROK_BOT',
            'cota_cursor': 'PAINEL_COTA_CURSOR'}
EXTRAS_FORA = ('whatsapp', 'gastos_hermes', 'presenca_processos', 'presenca_linux')


class CotasLocaisColetorTests(unittest.TestCase):
    VARIAVEIS = tuple(VARIAVEL.values()) + ("PAINEL_PRESENCA_LINUX", "PAINEL_PRESENCA", "PAINEL_GASTOS")

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_cotas_locais', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.dados = {
            'cota_codex_local': {'estado': 'ok', 'observado_epoch': 900.0, 'barras': []},
            'cota_grok_bot': {'estado': 'ok', 'observado_epoch': 900.0, 'barras': []},
            'cota_cursor': {'estado': 'ok', 'plano': 'pro', 'assinatura': 'active'}}
        self.fontes = {nome: Mock(return_value={nome: self.dados[nome]}) for nome in FONTES}
        self.fontes.update({
            'whatsapp': Mock(return_value={'whatsapp': {'estado': 'connected'}}),
            'gastos_hermes': Mock(return_value={'gastos_hermes': {'fixture': True}}),
            'presenca_processos': Mock(return_value={'presenca_processos': {
                'estado': 'ok', 'itens': [], 'ilegiveis': 0}}),
            'presenca_linux': Mock(return_value={'presenca_linux': {
                'estado': 'ok', 'host': 'Ubuntu-26.04', 'itens': []}})})
        for nome, valor in [('gravar', None), ('montar', {'agentes': []})]:
            guarda = (patch.object(self.coletor, nome, side_effect=lambda *a: {'agentes': []})
                      if nome == 'montar' else patch.object(self.coletor, nome, return_value=valor))
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
        return self.coletor.passada_viva(self.args, fontes=self.fontes,
                                        ctx={'agora': hora}, cache=self.cache)

    # 1
    def test_1_desligado_nao_registra_nao_chama_nao_publica(self):
        for valor in (None, '0', 'true'):
            with self.subTest(valor=valor), patch.dict(os.environ):
                self.ligar(**({} if valor is None else {v: valor for v in VARIAVEL.values()}))
                self.cache.clear()
                registradas = self.coletor.fontes_vivas(True)
                d = self.passada(1000)
                for nome in FONTES:
                    self.assertNotIn(nome, registradas)
                    self.assertNotIn(nome, self.coletor.extras_ativos())
                    self.assertNotIn(nome, d)
                    self.assertNotIn(nome, d['fontes'])
                    self.fontes[nome].assert_not_called()

    # 2
    def test_2_cada_variavel_sozinha_registra_e_publica_so_a_sua(self):
        for nome in FONTES:
            with self.subTest(fonte=nome), patch.dict(os.environ):
                self.ligar(**{VARIAVEL[nome]: '1'})
                self.cache.clear()
                self.assertEqual(self.coletor.extras_ativos(), (nome,))
                registradas = self.coletor.fontes_vivas(True)
                self.assertIn(nome, registradas)
                self.assertEqual(registradas[nome].__name__, 'ler')
                self.assertEqual(registradas[nome].__module__, 'fontes.' + nome)
                d = self.passada(1000)
                self.assertEqual(d[nome], self.dados[nome])
                self.assertIs(d['fontes'][nome]['ok'], True)
                for outra in tuple(f for f in FONTES if f != nome) + EXTRAS_FORA:
                    self.assertNotIn(outra, registradas)
                    self.assertNotIn(outra, d)
                    self.assertNotIn(outra, d['fontes'])

    # 3
    def test_3_tudo_ligado_ordem_exata_das_extras(self):
        self.ligar(PAINEL_GASTOS='1', PAINEL_PRESENCA='1', PAINEL_PRESENCA_LINUX='1',
                   PAINEL_COTA_CODEX_LOCAL='1', PAINEL_COTA_GROK_BOT='1', PAINEL_COTA_CURSOR='1')
        self.assertEqual(self.coletor.extras_ativos(),
                         ("whatsapp", "gastos_hermes", "presenca_processos", "presenca_linux",
                          "cota_codex_local", "cota_grok_bot", "cota_cursor"))

    # 4
    def test_4_cadencia_120_60_120(self):
        casos = (('cota_codex_local', 100, 121), ('cota_grok_bot', 50, 61), ('cota_cursor', 100, 121))
        for nome, dentro, fora in casos:
            with self.subTest(fonte=nome), patch.dict(os.environ):
                self.ligar(**{VARIAVEL[nome]: '1'})
                self.cache.clear()
                fonte = self.fontes[nome]
                fonte.reset_mock()
                self.passada(1000)
                self.assertEqual(fonte.call_count, 1)
                self.passada(1000 + dentro)
                self.assertEqual(fonte.call_count, 1)
                self.passada(1000 + fora)
                self.assertEqual(fonte.call_count, 2)

    # 5
    def test_5_falha_retem_ate_300s_e_depois_vira_erro(self):
        for nome in FONTES:
            with self.subTest(fonte=nome), patch.dict(os.environ):
                self.ligar(**{VARIAVEL[nome]: '1'})
                self.cache.clear()
                self.fontes[nome].return_value = {nome: self.dados[nome]}
                self.passada(1000)
                self.fontes[nome].return_value = {'erro': 'x'}
                for hora in (1120, 1240):
                    d = self.passada(hora)
                    self.assertEqual(d[nome], self.dados[nome])
                    self.assertIs(d['fontes'][nome]['desatualizado'], True)
                d = self.passada(1301)
                self.assertEqual(d[nome], {'estado': 'erro'})
                self.assertIs(d['fontes'][nome]['desatualizado'], False)


if __name__ == '__main__':
    unittest.main()
