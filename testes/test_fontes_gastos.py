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
                display_name TEXT, origin_json TEXT, cwd TEXT, session_key TEXT,
                thread_id TEXT, chat_type TEXT, billing_mode TEXT,
                id INTEGER PRIMARY KEY, model TEXT)''')
            db.execute('''CREATE TABLE messages (session_id INTEGER, role TEXT,
                timestamp REAL, content TEXT, api_content TEXT, reasoning TEXT)''')
            db.execute('CREATE INDEX messages_session ON messages(session_id)')
            db.execute('''CREATE TABLE session_model_usage (session_id INTEGER, model TEXT,
                billing_mode TEXT, task TEXT, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                actual_cost_usd REAL, estimated_cost_usd REAL)''')
            db.commit()

    def fonte(self):
        self.assertIsNotNone(importlib.util.find_spec('lib.fontes.gastos_hermes'))
        return importlib.import_module('lib.fontes.gastos_hermes')

    def inserir(self, source, ts, entrada=10, saida=5, leitura=3, escrita=2,
                raciocinio=1, estimado=0.5, real=None, mensagens=2,
                chat_type=None, billing_mode=None):
        with closing(sqlite3.connect(self.caminho)) as db:
            db.execute('''INSERT INTO sessions (source, started_at, input_tokens,
                output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens,
                estimated_cost_usd, actual_cost_usd, message_count, chat_type, billing_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (source, ts, entrada, saida, leitura, escrita, raciocinio, estimado, real,
                 mensagens, chat_type, billing_mode))
            db.commit()

    def modelos(self, linhas):
        with closing(sqlite3.connect(self.caminho)) as db:
            db.executemany('''INSERT INTO session_model_usage
                (session_id, model, input_tokens, output_tokens, cache_read_tokens,
                 cache_write_tokens, reasoning_tokens, task, billing_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', linhas)
            db.commit()

    def test_modelos_tokens_exatos_custo_rateado_e_canal_modelo(self):
        self.inserir('local', self.agora, real=8, entrada=999)
        self.modelos([(1, 'modelo-a', 10, 10, 10, 10, 2, '', None),
                      (1, 'modelo-b', 30, 30, 30, 30, 7, 'background_review', None)])
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        self.assertIs(d.get('custo_modelo_rateado'), True)
        modelos = {m['model']: m for m in d['7d']['modelos']}
        self.assertEqual(modelos['modelo-a']['cost_usd'], 2)
        self.assertEqual(modelos['modelo-b']['cost_usd'], 6)
        self.assertEqual(modelos['modelo-a']['input_tokens'], 10)
        self.assertEqual(modelos['modelo-b']['reasoning_tokens'], 7)
        self.assertEqual(modelos['modelo-b']['sub_rotulo'], 'revisão em segundo plano')
        self.assertEqual(d['7d']['origens'][0]['modelos'], d['7d']['modelos'])
        self.assertEqual(d['7d']['total']['input_tokens'], 40)

    def test_serie_meia_noite_somas_janelas_e_sessao_antiga(self):
        meia_noite = datetime(2026, 10, 30).timestamp()
        self.inserir('local', meia_noite - 40 * 86400, real=12)
        with closing(sqlite3.connect(self.caminho)) as db:
            db.executemany('INSERT INTO messages(session_id, role, timestamp) VALUES (1, ?, ?)',
                           [('assistant', meia_noite - 1800), ('assistant', meia_noite + 1800),
                            ('assistant', meia_noite + 2700), ('user', meia_noite + 1)])
            db.commit()
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        self.assertIn('24h', d)
        self.assertEqual(d['hoje']['total']['cost_usd'], 8)
        self.assertEqual(d['7d']['total']['cost_usd'], 12)
        dias = {b['rotulo']: b for b in d['7d']['serie']}
        self.assertEqual(dias['2026-10-29']['total'][0], 4)
        self.assertEqual(dias['2026-10-30']['total'][0], 8)
        horas = [b for b in d['24h']['serie'] if b['total'][0]]
        self.assertEqual([b['total'][0] for b in horas], [4, 8])
        self.assertTrue(d['distribuicao_aproximada'])
        for chave in ('hoje', '24h', '7d', '30d'):
            janela = d[chave]
            self.assertAlmostEqual(sum(b['total'][0] for b in janela['serie']), janela['total']['cost_usd'], delta=1e-4)
            self.assertAlmostEqual(sum(b['total'][1] for b in janela['serie']),
                                   sum(janela['total'][c] for c in self.fonte().CAMPOS[:4]))
            for b in janela['serie']:
                self.assertAlmostEqual(sum(v[0] for v in b['por_canal'].values()), b['total'][0], delta=1e-4)
                self.assertAlmostEqual(sum(v[1] for v in b['por_modelo'].values()), b['total'][1])
        self.assertEqual(len(d['7d']['serie']), 7)
        self.assertEqual(len(d['30d']['serie']), 30)

    def test_modelo_regex_fallback_task_e_custo_zero(self):
        nomes = ['Valido', '<script>', 'pessoa@example.org', 'a' * 65, 'modelo\n', None]
        for i, nome in enumerate(nomes, 1):
            self.inserir('local', self.agora, real=0, estimado=999)
            self.modelos([(i, nome, 1, 2, 3, 4, 5, 'segredo da tarefa', None)])
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        self.assertEqual([m['model'] for m in d['7d']['modelos']], ['outro modelo'])
        self.assertEqual(d['7d']['modelos'][0]['input_tokens'], len(nomes))
        self.assertEqual(d['7d']['total']['cost_usd'], 0)
        for nome in nomes[:-1] + ['segredo da tarefa']:
            self.assertNotIn(nome, json.dumps(d))

    def test_fallback_sessions_model_sem_assistant(self):
        self.inserir('cli', self.agora, real=3)
        with closing(sqlite3.connect(self.caminho)) as db:
            db.execute("UPDATE sessions SET model='modelo-fallback'")
            db.execute("INSERT INTO messages(session_id, role, timestamp) VALUES(1, 'user', ?)", (self.agora,))
            db.commit()
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        self.assertEqual(d['7d']['modelos'][0]['model'], 'modelo-fallback')
        self.assertEqual(d['7d']['modelos'][0]['input_tokens'], 10)
        self.assertEqual(d['24h']['serie'][-1]['total'][0], 3)

    def test_rateio_sem_tokens_e_custos_invalidos(self):
        self.inserir('local', self.agora, real=4)
        self.modelos([(1, 'a', 0, 0, 0, 0, 0, '', None),
                      (1, 'b', 0, 0, 0, 0, 0, '', None)])
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        self.assertEqual([m['cost_usd'] for m in d['hoje']['modelos']], [2, 2])
        for valor in (-1, float('inf'), float('nan')):
            with self.subTest(valor=valor):
                with closing(sqlite3.connect(self.caminho)) as db:
                    db.execute('UPDATE sessions SET actual_cost_usd=?, estimated_cost_usd=0', (valor,))
                    db.commit()
                d = self.fonte().ler(self.ctx)['gastos_hermes']
                self.assertEqual(d['hoje']['total']['cost_usd'], 0)
                json.dumps(d, allow_nan=False)

    def test_limite_24h_parcial_e_denominador_fora_da_janela(self):
        self.ctx['agora'] = self.agora + 123
        inicio = self.ctx['agora'] - 86400
        self.inserir('local', inicio - 10, real=9)
        with closing(sqlite3.connect(self.caminho)) as db:
            db.executemany("INSERT INTO messages(session_id, role, timestamp) VALUES(1, 'assistant', ?)",
                           [(inicio - 1,), (inicio,), (self.ctx['agora'],), (self.ctx['agora'] + 1,)])
            db.commit()
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        self.assertEqual(d['24h']['desde'], inicio)
        self.assertEqual(d['24h']['total']['cost_usd'], 6)
        self.assertEqual(d['24h']['serie'][0]['total'][0], 3)
        self.assertEqual(d['24h']['serie'][-1]['total'][0], 3)
        self.assertEqual(sum(b['total'][0] for b in d['24h']['serie']), 6)
        self.assertEqual(d['7d']['total']['cost_usd'], 9)

    def test_json_menor_64kb_e_excesso_falha_seguro(self):
        for i in range(30):
            self.inserir('local', self.agora - i * 86400)
            self.modelos([(i + 1, 'modelo-a', 10, 2, 3, 4, 0, '', None),
                          (i + 1, 'modelo-b', 30, 2, 3, 4, 0, '', None)])
        d = self.fonte().ler(self.ctx)
        self.assertIn('gastos_hermes', d)
        self.assertLess(len(json.dumps(d).encode('utf-8')), 64 * 1024)
        self.modelos([(1, 'modelo-' + str(i), 1, 0, 0, 0, 0, '', None) for i in range(300)])
        d = self.fonte().ler(self.ctx)
        self.assertIn('erro', d)
        self.assertLess(len(json.dumps(d).encode('utf-8')), 64 * 1024)
        self.assertNotIn('gastos_hermes', d)

    def test_whitelist_completa_de_canais_sem_texto_cru(self):
        casos = [
            ('local', None, 'Terminal'), ('cli', None, 'Terminal'),
            ('whatsapp', 'dm', 'WhatsApp · conversa direta'),
            ('whatsapp', 'group', 'WhatsApp · grupo'),
            ('whatsapp', 'channel', 'WhatsApp · grupo'),
            ('whatsapp', 'thread', 'WhatsApp · grupo'),
            ('whatsapp', None, 'WhatsApp'),
            ('whatsapp', 'cliente_confidencial', 'WhatsApp'),
            ('subagent', None, 'Subagentes'), ('tool', None, 'Ferramentas'),
            ('oneshot', None, 'One-shot'), ('desktop', None, 'App desktop'),
            ('cliente_confidencial', 'dm', 'Outra'), ('WHATSAPP', 'dm', 'Outra'),
            (None, None, 'Outra'), ('', None, 'Outra'),
        ]
        for source, chat_type, esperado in casos:
            with self.subTest(source=source, chat_type=chat_type):
                with closing(sqlite3.connect(self.caminho)) as db:
                    db.execute('DELETE FROM sessions')
                    db.commit()
                self.inserir(source, self.agora, chat_type=chat_type,
                             billing_mode='cliente_confidencial')
                d = self.fonte().ler(self.ctx)['gastos_hermes']
                for janela in ('hoje', '7d'):
                    self.assertEqual([o['source'] for o in d[janela]['origens']], [esperado])
                self.assertNotIn('cliente_confidencial', json.dumps(d))

    def test_assinatura_por_canal_sem_alterar_custo_ou_tokens(self):
        self.inserir('subagent', self.agora, real=0, estimado=99,
                     billing_mode='subscription_included')
        self.inserir('local', self.agora, real=0, billing_mode='subscription_included')
        self.inserir('cli', self.agora, estimado=0.7)
        self.inserir('tool', self.agora, real=0, billing_mode='cliente_confidencial')
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        for janela in ('hoje', '7d'):
            canais = {o['source']: o for o in d[janela]['origens']}
            self.assertIs(canais['Subagentes'].get('inclui_assinatura'), True)
            self.assertEqual(canais['Subagentes']['cost_usd'], 0)
            self.assertEqual(canais['Subagentes']['input_tokens'], 10)
            self.assertIs(canais['Terminal']['inclui_assinatura'], True)
            self.assertAlmostEqual(canais['Terminal']['cost_usd'], 0.7)
            self.assertEqual(canais['Terminal']['input_tokens'], 20)
            self.assertIs(canais['Ferramentas']['inclui_assinatura'], False)
            self.assertAlmostEqual(d[janela]['total']['cost_usd'], 0.7)
            for campo in self.fonte().CAMPOS:
                self.assertAlmostEqual(sum(o[campo] for o in canais.values()), d[janela]['total'][campo],
                                       delta=1e-4)
        self.assertNotIn('cliente_confidencial', json.dumps(d))
        self.assertNotIn('subscription_included', json.dumps(d))

    def test_agrega_hoje_7d_actual_senao_estimated(self):
        hoje = datetime.fromtimestamp(self.agora).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        # E10: sete datas locais, incluindo hoje (não sete períodos de 24h).
        semana = hoje - 6 * 86400
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
        self.assertEqual([o['source'] for o in d['hoje']['origens']], ['WhatsApp'])
        self.assertEqual([o['source'] for o in d['7d']['origens']], ['Terminal', 'WhatsApp'])
        self.assertEqual(d['hoje']['total']['input_tokens'], 20)
        self.assertEqual(d['hoje']['total']['output_tokens'], 10)
        self.assertEqual(d['hoje']['total']['cache_read_tokens'], 6)
        self.assertEqual(d['hoje']['total']['cache_write_tokens'], 4)
        self.assertEqual(d['hoje']['total']['reasoning_tokens'], 2)
        self.assertEqual(d['hoje']['total']['message_count'], 4)
        self.assertAlmostEqual(d['hoje']['total']['cost_usd'], 0.2)
        self.assertAlmostEqual(d['7d']['total']['cost_usd'], 1.2)

    def test_custo_sai_com_ate_6_casas_sem_arredondar_tokens(self):
        self.inserir('local', self.agora, real=1 / 3, entrada=7)
        self.modelos([(1, 'modelo-a', 1, 1, 1, 1, 0, '', None),
                      (1, 'modelo-b', 2, 2, 2, 2, 0, '', None)])
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        j = d['7d']
        custos = [j['total']['cost_usd'], j['origens'][0]['cost_usd']]
        custos += [m['cost_usd'] for m in j['modelos'] + j['origens'][0]['modelos']]
        for b in j['serie']:
            custos += [b['total'][0]] + [v[0] for v in b['por_canal'].values()]
            custos += [v[0] for v in b['por_modelo'].values()]
        self.assertTrue(any(custos))
        for c in custos:
            self.assertEqual(c, round(c, 6))
        self.assertEqual(j['total']['cost_usd'], 0.333333)
        self.assertEqual(j['total']['input_tokens'], 3)

    def test_banco_vazio_e_nulos(self):
        d = self.fonte().ler(self.ctx)['gastos_hermes']
        for janela in ('hoje', '7d'):
            self.assertEqual(d[janela]['origens'], [])
            self.assertEqual(set(d[janela]['total'].values()), {0})
        self.inserir(None, self.agora, entrada=None, saida=None, leitura=None,
                     escrita=None, raciocinio=None, estimado=None, mensagens=None)
        d = self.fonte().ler(self.ctx)['gastos_hermes']['hoje']
        self.assertEqual(d['origens'][0]['source'], 'Outra')
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
        permitidas = {'sessions': {'id', 'model', 'source', 'started_at', 'input_tokens', 'output_tokens',
                     'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens',
                     'estimated_cost_usd', 'actual_cost_usd', 'message_count', 'chat_type', 'billing_mode'},
                     'session_model_usage': {'session_id', 'model', 'billing_mode', 'task',
                         'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'},
                     'messages': {'session_id', 'role', 'timestamp'}}

        def autorizar(acao, tabela, coluna, *resto):
            if acao == sqlite3.SQLITE_READ:
                acessos.append((tabela, coluna))
                return sqlite3.SQLITE_OK if coluna in permitidas.get(tabela, set()) else sqlite3.SQLITE_DENY
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
        for tabela, colunas in permitidas.items():
            self.assertEqual({coluna for t, coluna in acessos if t == tabela}, colunas)
        with self.assertRaises(sqlite3.ProgrammingError):
            conexoes[0].execute('SELECT 1')

    def test_source_pessoal_agrupado_sem_vazamento(self):
        self.inserir('5511999998888@s.whatsapp.net', self.agora)
        self.inserir('pessoa@example.org', self.agora)
        self.inserir('<script>', self.agora)
        d = self.fonte().ler(self.ctx)['gastos_hermes']['hoje']
        self.assertEqual(len(d['origens']), 1)
        self.assertEqual(d['origens'][0]['source'], 'Outra')
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
