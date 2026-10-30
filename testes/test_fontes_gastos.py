"""Gastos Hermes somente em bancos sintéticos e coletor com fontes injetadas."""
from contextlib import closing
from datetime import datetime
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock


class GastosTests(unittest.TestCase):
    def setUp(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.caminho = Path(pasta.name) / 'state # teste.db'
        self.agora = datetime(2026, 10, 30, 12).timestamp()
        self.ctx = {'gastos_path': self.caminho, 'agora': self.agora}
        with closing(sqlite3.connect(self.caminho)) as db:
            db.execute('''CREATE TABLE sessions (
                source TEXT, started_at REAL, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                estimated_cost_usd REAL, actual_cost_usd REAL, message_count INTEGER,
                title TEXT, system_prompt TEXT, chat_id TEXT, user_id TEXT,
                display_name TEXT, origin_json TEXT, cwd TEXT)''')
            db.execute('CREATE TABLE messages (body TEXT)')
            db.commit()

    def fonte(self):
        self.assertIsNotNone(importlib.util.find_spec('lib.fontes.gastos_hermes'))
        return importlib.import_module('lib.fontes.gastos_hermes')

    def inserir(self, source, ts, entrada=10, saida=5, leitura=3, escrita=2,
                raciocinio=1, estimado=0.5, real=None, mensagens=2):
        with closing(sqlite3.connect(self.caminho)) as db:
            db.execute('''INSERT INTO sessions (source, started_at, input_tokens,
                output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens,
                estimated_cost_usd, actual_cost_usd, message_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (source, ts, entrada, saida, leitura, escrita, raciocinio, estimado, real, mensagens))
            db.commit()

    def test_agrega_hoje_7d_actual_senao_estimated(self):
        hoje = datetime.fromtimestamp(self.agora).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        semana = self.agora - 7 * 86400
        self.inserir('whatsapp', hoje, real=0.2)
        self.inserir('whatsapp', self.agora, real=0)
        self.inserir('cli', hoje - 1, estimado=0.7)
        self.inserir('local', semana, estimado=0.3)
        self.inserir('oneshot', semana - 1, real=900)
        self.inserir('futuro', self.agora + 1, real=900)
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        self.assertEqual(d['custo_tipo'], 'estimado pelo Hermes')
        self.assertEqual(d['hoje']['desde'], hoje)
        self.assertEqual(d['7d']['desde'], semana)
        self.assertEqual([o['source'] for o in d['hoje']['origens']], ['whatsapp'])
        self.assertEqual([o['source'] for o in d['7d']['origens']], ['cli', 'local', 'whatsapp'])
        self.assertEqual(d['hoje']['total']['input_tokens'], 20)
        self.assertEqual(d['hoje']['total']['output_tokens'], 10)
        self.assertEqual(d['hoje']['total']['cache_read_tokens'], 6)
        self.assertEqual(d['hoje']['total']['cache_write_tokens'], 4)
        self.assertEqual(d['hoje']['total']['reasoning_tokens'], 2)
        self.assertEqual(d['hoje']['total']['message_count'], 4)
        self.assertAlmostEqual(d['hoje']['total']['cost_usd'], 0.2)
        self.assertAlmostEqual(d['7d']['total']['cost_usd'], 1.2)

    def test_banco_vazio_e_nulos(self):
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        for janela in ('hoje', '7d'):
            self.assertEqual(d[janela]['origens'], [])
            self.assertEqual(set(d[janela]['total'].values()), {0})
        self.inserir(None, self.agora, entrada=None, saida=None, leitura=None,
                     escrita=None, raciocinio=None, estimado=None, mensagens=None)
        d = self.fonte().ler(self.ctx)['gastos_hermes']['hoje']
        self.assertEqual(d['origens'][0]['source'], 'desconhecida')
        self.assertEqual(set(d['total'].values()), {0})

    def test_banco_ausente_nao_criado(self):
        self.caminho.unlink()
        d = self.fonte().ler(self.ctx)
        self.assertTrue(d['erro'])
        self.assertFalse(self.caminho.exists())
        self.assertNotIn('gastos_hermes', d)

    def test_banco_travado(self):
        with closing(sqlite3.connect(self.caminho)) as escritor:
            escritor.execute('BEGIN EXCLUSIVE')
            try:
                d = self.fonte().ler(self.ctx)
                self.assertTrue(d['erro'])
                self.assertNotIn(str(self.caminho), d['erro'])
            finally:
                escritor.rollback()

    def test_query_so_colunas_permitidas_e_conexao_readonly(self):
        fonte = self.fonte()
        real_connect = sqlite3.connect
        acessos, conexoes = [], []
        permitidas = {'source', 'started_at', 'input_tokens', 'output_tokens',
                     'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens',
                     'estimated_cost_usd', 'actual_cost_usd', 'message_count'}

        def autorizar(acao, tabela, coluna, *resto):
            if acao == sqlite3.SQLITE_READ:
                acessos.append((tabela, coluna))
                return sqlite3.SQLITE_OK if tabela == 'sessions' and coluna in permitidas else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def conectar(database, **kwargs):
            self.assertTrue(database.startswith('file:'))
            self.assertTrue(database.endswith('?mode=ro'))
            self.assertTrue(kwargs['uri'])
            self.assertLessEqual(kwargs['timeout'], 2)
            db = real_connect(database, **kwargs)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('CREATE TABLE proibida (x)')
            db.set_authorizer(autorizar)
            conexoes.append(db)
            return db

        with patch.object(fonte.sqlite3, 'connect', side_effect=conectar):
            self.assertNotIn('erro', fonte.ler(self.ctx))
        self.assertTrue(acessos)
        self.assertEqual({coluna for _, coluna in acessos}, permitidas)
        with self.assertRaises(sqlite3.ProgrammingError):
            conexoes[0].execute('SELECT 1')

    def test_source_pessoal_agrupado_sem_vazamento(self):
        self.inserir('5511999998888@s.whatsapp.net', self.agora)
        self.inserir('pessoa@example.org', self.agora)
        self.inserir('<script>', self.agora)
        d = self.fonte().ler(self.ctx)['gastos_hermes']['hoje']
        self.assertEqual(len(d['origens']), 1)
        self.assertEqual(d['origens'][0]['source'], 'desconhecida')
        self.assertEqual(d['origens'][0]['input_tokens'], 30)
        self.assertNotIn('5511999998888', json.dumps(d))
        self.assertNotIn('pessoa@', json.dumps(d))


class IntegracaoGastosTests(unittest.TestCase):
    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_gastos', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.fontes = {'whatsapp': Mock(return_value={'whatsapp': {'estado': 'connected'}}),
                       'gastos_hermes': Mock(return_value={'gastos_hermes': {'fixture': True}})}
        for nome, valor in [('gravar', None), ('montar', {'agentes': []})]:
            guarda = (patch.object(self.coletor, nome, side_effect=lambda *a: {'agentes': []})
                      if nome == 'montar' else patch.object(self.coletor, nome, return_value=valor))
            guarda.start()
            self.addCleanup(guarda.stop)

    def passada(self, hora):
        return self.coletor.passada_viva(self.args, fontes=self.fontes,
                                        ctx={'agora': hora}, cache=self.cache)

    def test_opt_in_ausente_ou_zero_nao_le_nem_publica_fontes(self):
        for valor in (None, '0', 'true'):
            with self.subTest(valor=valor), patch.dict(os.environ):
                os.environ.pop('PAINEL_GASTOS', None)
                if valor is not None:
                    os.environ['PAINEL_GASTOS'] = valor
                self.assertNotIn('whatsapp', self.coletor.fontes_vivas(True))
                self.assertNotIn('gastos_hermes', self.coletor.fontes_vivas(True))
                d = self.passada(1000)
                for chave in self.fontes:
                    self.assertNotIn(chave, d)
                    self.assertNotIn(chave, d['fontes'])
                    self.fontes[chave].assert_not_called()

    @patch.dict(os.environ, {'PAINEL_GASTOS': '1'})
    def test_opt_in_registra_e_publica_com_cadencia(self):
        registradas = self.coletor.fontes_vivas(True)
        self.assertIn('whatsapp', registradas)
        self.assertIn('gastos_hermes', registradas)
        d = self.passada(1000)
        for chave in self.fontes:
            self.assertIn(chave, d)
            self.assertEqual(set(d['fontes'][chave]),
                             {'ok', 'erro', 'hora', 'ultima_leitura_ok', 'desatualizado'})
        self.passada(1009)
        self.passada(1010)
        self.passada(1059)
        self.assertEqual(self.fontes['gastos_hermes'].call_count, 1)
        self.assertEqual(self.fontes['whatsapp'].call_count, 3)
        self.passada(1060)
        self.assertEqual(self.fontes['gastos_hermes'].call_count, 2)

    @patch.dict(os.environ, {'PAINEL_GASTOS': '1'})
    def test_falha_retem_300s_expira_e_recupera(self):
        self.passada(1000)
        self.fontes['gastos_hermes'].return_value = {'erro': 'banco indisponível'}
        d = self.passada(1060)
        self.assertTrue(d['gastos_hermes']['fixture'])
        self.assertTrue(d['fontes']['gastos_hermes']['desatualizado'])
        self.assertFalse(d['fontes']['gastos_hermes']['ok'])
        self.assertEqual(d['fontes']['gastos_hermes']['ultima_leitura_ok'], 1000)
        d = self.passada(1300)
        self.assertTrue(d['gastos_hermes']['fixture'])
        d = self.passada(1301)
        self.assertNotIn('fixture', d['gastos_hermes'])
        self.assertFalse(d['fontes']['gastos_hermes']['desatualizado'])
        self.fontes['gastos_hermes'].return_value = {'gastos_hermes': {'fixture': True}}
        d = self.passada(1361)
        self.assertTrue(d['fontes']['gastos_hermes']['ok'])

    @patch.dict(os.environ, {'PAINEL_GASTOS': '1'})
    def test_desligar_ignora_cache_existente(self):
        self.passada(1000)
        with patch.dict(os.environ, {'PAINEL_GASTOS': '0'}):
            d = self.passada(1001)
        for chave in self.fontes:
            self.assertNotIn(chave, d)
            self.assertNotIn(chave, d['fontes'])
            self.fontes[chave].assert_called_once()
