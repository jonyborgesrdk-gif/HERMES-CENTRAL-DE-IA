"""Contratos E25: dinheiro, datas locais, privacidade, corrupção e integração."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib.fontes import custo_agentes as mod
from lib.fontes import gastos_hermes


RAIZ = Path(__file__).resolve().parents[1]
AGORA = datetime(2026, 11, 19, 18).timestamp()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def claude(ts=AGORA-60, ident='m1', nome='claude-opus-5-5', **uso):
    return {'timestamp': iso(ts), 'cwd': r'C:\SEGREDO-PASTA-X', 'session_id': 'SEGREDO-ID-Y',
            'message': {'id': ident, 'model': nome, 'content': 'SEGREDO-PROMPT-Z',
                        'usage': dict(input_tokens=100, output_tokens=10, **uso)}}


def codex(ts=AGORA-60, i=100, c=30, w=20, o=10, r=4, **extras):
    return {'timestamp': iso(ts), 'type': 'event_msg', 'payload': {'type': 'token_count',
            'info': {'last_token_usage': dict(input_tokens=i, cached_input_tokens=c,
                    cache_write_input_tokens=w, output_tokens=o, reasoning_output_tokens=r,
                    total_tokens=i+o, **extras), 'total_token_usage': {'input_tokens': 999999999}}}}


def contexto(nome='gpt-6.1-sol'):
    return {'type': 'turn_context', 'payload': {'model': nome, 'cwd': r'C:\SEGREDO-PASTA-X'}}


class CustoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.ctx = dict(dir=self.raiz, agora=AGORA, claude_dir=self.raiz/'claude',
                        codex_dir=self.raiz/'codex', grok_dir=self.raiz/'grok',
                        gastos_path=self.raiz/'state.db')
        self.env = patch.dict(os.environ, PAINEL_CUSTO='1')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cambio = patch.object(mod.gastos, 'cotacao', return_value={
            'usd_brl': 5, 'data_epoch': AGORA, 'fonte': 'manual', 'desatualizada': False})
        self.cambio.start()
        self.addCleanup(self.cambio.stop)
        mod._CACHE.clear()
        mod._HERMES_CACHE.clear()
        self.banco()

    def banco(self, linhas=()):
        with closing(sqlite3.connect(self.ctx['gastos_path'])) as db, db:
            db.executescript('''
                CREATE TABLE sessions (id TEXT, source TEXT, chat_type TEXT, billing_mode TEXT,
                  started_at REAL, model TEXT, input_tokens INT, output_tokens INT,
                  cache_read_tokens INT, cache_write_tokens INT, reasoning_tokens INT,
                  message_count INT, actual_cost_usd REAL, estimated_cost_usd REAL);
                CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_mode TEXT,
                  task TEXT, input_tokens INT, output_tokens INT, cache_read_tokens INT,
                  cache_write_tokens INT, reasoning_tokens INT);
                CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL);
            ''')
            for linha in linhas:
                db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', linha)

    def arquivo(self, agente, dados, nome='SEGREDO-ID-Y.jsonl', mtime=AGORA):
        raiz = self.ctx[agente+'_dir']
        if agente == 'grok':
            raiz = raiz/'sessions/SEGREDO-PASTA-X/SEGREDO-ID-Y'
        p = raiz/nome
        p.parent.mkdir(parents=True, exist_ok=True)
        conteudo = json.dumps(dados) if agente == 'grok' else '\n'.join(json.dumps(d) for d in dados)+'\n'
        if nome.endswith('.zst'):
            from compression import zstd
            p.write_bytes(zstd.compress(conteudo.encode()))
        else:
            p.write_text(conteudo, encoding='utf-8')
        os.utime(p, (mtime, mtime))
        return p

    def ler(self):
        return mod.ler(self.ctx)['custo_agentes']

    def agente(self, r, nome='claude'):
        return next(a for a in r['agentes'] if a['agente'] == nome)

    def test_opt_in_exato_na_fonte_sem_io(self):
        for valor in ('', '0', 'true', 'sim'):
            with self.subTest(valor=valor), patch.dict(os.environ, PAINEL_CUSTO=valor), \
                    patch.object(mod, 'arquivos', side_effect=AssertionError('não ler')):
                self.assertEqual(mod.ler(self.ctx), {})

    def test_claude_dedupe_ultima_ocorrencia_nao_maior_contador(self):
        a = claude()
        b = claude()
        b['message']['usage']['input_tokens'] = 50
        self.arquivo('claude', [a, b])
        r = self.ler()
        self.assertEqual(r['hoje']['tokens']['entrada'], 50)
        self.assertEqual(r['hoje']['tokens']['saida'], 10)
        self.assertAlmostEqual(r['hoje']['usd'], (50*4+10*20)/1e6)

    def test_claude_dedupe_entre_arquivos_e_synthetic(self):
        self.arquivo('claude', [claude()], nome='a.jsonl')
        b = claude(ts=AGORA-30)
        b['message']['usage']['input_tokens'] = 75
        self.arquivo('claude', [b, claude(nome='<synthetic>', ident='s')], nome='b.jsonl')
        self.assertEqual(self.ler()['hoje']['tokens']['entrada'], 75)

    def test_claude_conta_manual_cache_5m_1h_leitura_saida(self):
        self.arquivo('claude', [claude(cache_creation_input_tokens=500,
            cache_creation={'ephemeral_5m_input_tokens': 200, 'ephemeral_1h_input_tokens': 300},
            cache_read_input_tokens=400)])
        r = self.ler()
        self.assertEqual(r['hoje']['usd'], 0.00408)  # 400+1000+2400+80+200 / 1M
        self.assertEqual(r['hoje']['brl'], 0.0204)
        self.assertEqual(r['hoje']['tokens']['total'], 1010)

    def test_claude_iterations_com_topo_zerado_soma_tipos_e_custo_manual(self):
        d = claude()
        d['message']['usage'] = dict.fromkeys(('input_tokens', 'cache_creation_input_tokens',
                                              'cache_read_input_tokens', 'output_tokens'), 0)
        d['message']['usage'].update(cache_creation={'ephemeral_1h_input_tokens': 99999}, iterations=[
            {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 20,
             'cache_creation_input_tokens': 70,
             'cache_creation': {'ephemeral_5m_input_tokens': 30, 'ephemeral_1h_input_tokens': 40}},
            {'input_tokens': 100, 'output_tokens': 50, 'cache_read_input_tokens': 200,
             'cache_creation_input_tokens': 700,
             'cache_creation': {'ephemeral_5m_input_tokens': 300, 'ephemeral_1h_input_tokens': 400}}])
        self.arquivo('claude', [d, d])  # dedupe também vale no formato iterations
        r = self.ler()
        self.assertEqual(r['hoje']['tokens'], mod.tokens(entrada=110, saida=55,
            cache_leitura=220, cache_escrita_5m=330, cache_escrita_1h=440))
        # (110*4 + 55*20 + 220*.2 + 330*5 + 440*8) / 1M
        self.assertEqual(r['hoje']['usd'], .006754)
        self.assertEqual(r['hoje']['brl'], .03377)
        self.assertFalse(r['hoje']['parcial'])

    def test_claude_topo_normal_com_iterations_nao_duplica(self):
        d = claude(cache_creation_input_tokens=500,
                   cache_creation={'ephemeral_5m_input_tokens': 200, 'ephemeral_1h_input_tokens': 300},
                   cache_read_input_tokens=400)
        d['message']['usage']['iterations'] = [dict(d['message']['usage'])]
        self.arquivo('claude', [d])
        r = self.ler()
        self.assertEqual(r['hoje']['usd'], .00408)
        self.assertEqual(r['hoje']['tokens']['total'], 1010)

    def test_haiku_limite_100k_e_acima_por_prompt_inclui_cache(self):
        a = claude(nome='claude-haiku-5-5', cache_read_input_tokens=99900)
        b = claude(nome='claude-haiku-5-5', ident='m2', cache_read_input_tokens=99901)
        self.arquivo('claude', [a, b])
        esperado = (100*.1+99900*.01+10*.5+100*.5+99901*.05+10*2.5)/1e6
        self.assertAlmostEqual(self.ler()['hoje']['usd'], esperado, places=8)

    def test_codex_formula_entrada_contem_cache_e_raciocinio_subconjunto_saida(self):
        self.arquivo('codex', [contexto(), codex()])
        r = self.ler()
        self.assertEqual(r['hoje']['tokens']['entrada'], 50)
        self.assertEqual(r['hoje']['tokens']['raciocinio'], 4)
        self.assertEqual(r['hoje']['tokens']['total'], 110)
        self.assertEqual(r['hoje']['usd'], 0.000253)  # 50*2 +30*.1+20*2.5+10*10

    def test_codex_modelo_precedente_divisao_dia_local_e_zstd(self):
        ontem = datetime.fromtimestamp(AGORA).replace(hour=0).timestamp()-1
        hoje = ontem+2
        self.arquivo('codex', [contexto(), codex(ontem), contexto('gpt-6.2-astra'), codex(hoje)],
                     nome='arquivo.jsonl.zst')
        r = self.ler()
        self.assertEqual(r['dias'][-2]['total']['usd'], 0.000253)
        self.assertEqual(r['dias'][-1]['total']['usd'], 0.001012)
        self.assertEqual(r['sete_dias']['tokens']['total'], 220)
        self.assertEqual(r['sessoes'][0]['inicio_epoch'], ontem)
        self.assertEqual(r['sessoes'][0]['tokens']['total'], 110)

    def test_codex_contexto_longo_272k_usa_tarifa_curta(self):
        self.arquivo('codex', [contexto(), codex(i=272001)])
        r = self.ler()
        self.assertIn('contexto_longo_nao_ajustado', r['sessoes'][0]['flags'])
        self.assertAlmostEqual(r['hoje']['usd'], ((272001-50)*2+3+50+100)/1e6)

    def test_codex_compactacao_sem_componentes_nao_inventa_tokens_ou_custo(self):
        d = codex(i=0, c=0, w=0, o=0, r=0)
        d['payload']['info']['last_token_usage']['total_tokens'] = 50091
        self.arquivo('codex', [contexto(), {'type': 'compacted', 'payload': {}}, d])
        r = self.ler()
        self.assertEqual(r['hoje']['tokens'], mod.tokens())
        self.assertEqual(r['hoje']['usd'], 0)
        self.assertNotIn('tokens_inconsistentes', r['sessoes'][0]['flags'])
        self.assertFalse(r['hoje']['parcial'])
        self.assertEqual(r['hoje']['eventos_sem_custo'], 0)

    def test_codex_compactacao_nao_repete_total_cumulativo_anterior(self):
        a = codex()
        b = codex(i=0, c=0, w=0, o=0, r=0)
        b['payload']['info']['last_token_usage']['total_tokens'] = 50091
        self.arquivo('codex', [contexto(), a, {'type': 'compacted'}, b])
        r = self.ler()
        self.assertEqual(r['hoje']['tokens']['total'], 110)
        self.assertEqual(r['hoje']['usd'], .000253)
        self.assertEqual(r['hoje']['flags'], [])

    def test_codex_272k_exatos_sem_flag_e_evento_cota_sem_info(self):
        self.arquivo('codex', [contexto(), codex(i=272000),
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None}}])
        r = self.ler()
        self.assertNotIn('contexto_longo_nao_ajustado', r['sessoes'][0]['flags'])
        self.assertEqual(r['registros_ignorados'], 0)

    def test_grok_ticks_endedat_e_selo_sem_tarifa(self):
        self.arquivo('grok', {'session': {'costUsdTicks': 999999999999}, 'turns': [
            {'endedAt': iso(AGORA-1), 'costUsdTicks': 12345678901, 'primaryModelId': 'grok-4.7-build',
             'inputTokens': 100, 'outputTokens': 20, 'cachedReadTokens': 30, 'cacheCreationTokens': 40,
             'reasoningTokens': 10, 'totalTokens': 190}]}, nome='usage.json')
        r = self.ler()
        self.assertEqual(r['hoje']['usd'], 1.23456789)
        self.assertEqual(r['hoje']['tokens']['total'], 190)
        self.assertEqual(self.agente(r, 'grok')['selo'], 'informado pelo Grok')
        self.assertNotIn('sem_tarifa', r['hoje']['flags'])

    def test_modelo_sem_tarifa_custo_nulo_tokens_preservados(self):
        self.arquivo('claude', [claude(nome='modelo-novo')])
        r = self.ler()
        self.assertIsNone(r['hoje']['usd'])
        self.assertIsNone(r['hoje']['brl'])
        self.assertEqual(r['hoje']['tokens']['total'], 110)
        self.assertIn('sem_tarifa', r['sessoes'][0]['flags'])
        self.assertTrue(r['avisos'])
        for d in (r['hoje'], r['sete_dias'], self.agente(r)['hoje'],
                  self.agente(r)['sete_dias'], r['dias'][-1]['total'],
                  r['dias'][-1]['agentes'][0], r['sessoes'][0]):
            self.assertTrue(d['parcial'])
            self.assertEqual(d['eventos_sem_custo'], 1)
            self.assertIsNone(d['usd'])
            self.assertIsNone(d['brl'])

    def test_custo_parcial_em_todos_os_agregados_preserva_soma_conhecida(self):
        self.arquivo('claude', [claude(), claude(ident='m2', nome='modelo-novo')])
        self.arquivo('codex', [contexto(), codex()])
        r = self.ler()
        for d in (self.agente(r)['hoje'], self.agente(r)['sete_dias'],
                  r['dias'][-1]['agentes'][0], next(s for s in r['sessoes'] if s['agente']=='claude')):
            self.assertEqual(d['usd'], .0006)
            self.assertEqual(d['brl'], .003)
            self.assertTrue(d['parcial'])
            self.assertEqual(d['eventos_sem_custo'], 1)
        for d in (r['hoje'], r['sete_dias'], r['dias'][-1]['total']):
            self.assertEqual(d['usd'], .000853)
            self.assertEqual(d['brl'], .004265)
            self.assertTrue(d['parcial'])
            self.assertEqual(d['eventos_sem_custo'], 1)
        self.assertNotIn('_eventos_com_custo', json.dumps(r))

    def test_parcial_acumula_eventos_sem_custo_em_dias_distintos(self):
        self.arquivo('claude', [claude(), claude(ident='m2', nome='modelo-novo'),
            claude(ts=AGORA-86400, ident='m3', nome='modelo-novo')])
        r = self.ler()
        self.assertEqual(r['hoje']['eventos_sem_custo'], 1)
        self.assertEqual(r['sete_dias']['eventos_sem_custo'], 2)
        self.assertEqual(self.agente(r)['sete_dias']['eventos_sem_custo'], 2)
        self.assertEqual(r['sete_dias']['usd'], .0006)
        self.assertIsNone(r['dias'][-2]['total']['usd'])

    def test_parcial_custo_conhecido_zero_continua_zero(self):
        d = claude()
        d['message']['usage'].update(input_tokens=0, output_tokens=0)
        self.arquivo('claude', [d, claude(ident='m2', nome='modelo-novo')])
        r = self.ler()
        self.assertEqual(r['hoje']['usd'], 0)
        self.assertEqual(r['hoje']['brl'], 0)
        self.assertTrue(r['hoje']['parcial'])

    def test_tarifas_usuario_sobrepoe_por_modelo(self):
        t, _ = mod.tarifas(self.ctx)
        v = dict(t['claude-opus-5-5'], entrada=9)
        (self.raiz/'tarifas.json').write_text(json.dumps({'modelos': {'claude-opus-5-5': v}}), encoding='utf-8')
        self.arquivo('claude', [claude()])
        self.assertEqual(self.ler()['hoje']['usd'], .0011)
        self.assertIn('gpt-6.1-sol', mod.tarifas(self.ctx)[0])

    def test_tarifas_usuario_modelos_vazio_valido_sem_aviso(self):
        (self.raiz/'tarifas.json').write_text('{"modelos": {}}', encoding='utf-8')
        self.arquivo('claude', [claude()])
        r = self.ler()
        self.assertEqual(r['hoje']['usd'], .0006)
        self.assertEqual(r['avisos'], [])

    def test_tarifas_invalidas_caem_no_padrao_aviso_constante(self):
        self.arquivo('claude', [claude()])
        for invalido in ('SEGREDO-PROMPT-Z', '{}', 'null', '{"modelos": []}',
                         '{"modelos": {"SEGREDO-ID-Y": {"entrada": -1}}}'):
            (self.raiz/'tarifas.json').write_text(invalido, encoding='utf-8')
            r = self.ler()
            self.assertEqual(r['hoje']['usd'], .0006)
            self.assertIn(mod.AVISO_TARIFAS, r['avisos'])

    def test_tarifa_velha_so_mais_de_30_dias(self):
        self.arquivo('claude', [claude()])
        t, _ = mod.tarifas(self.ctx)
        for dias, velha in ((30, False), (31, True)):
            t['claude-opus-5-5']['conferido_em'] = (datetime.fromtimestamp(AGORA).date()-timedelta(days=dias)).isoformat()
            (self.raiz/'tarifas.json').write_text(json.dumps({'modelos': t}), encoding='utf-8')
            r = self.ler()
            self.assertEqual(r['tarifas'][0]['tarifa_velha'], velha)
            self.assertEqual('tarifa conferida há mais de 30 dias' in r['avisos'], velha)

    def test_preco_negativo_nan_campos_extras_invalidam_arquivo_inteiro(self):
        t, _ = mod.tarifas(self.ctx)
        self.arquivo('claude', [claude()])
        for mudanca in ({'entrada': -1}, {'entrada': float('nan')}, {'total': 9999}):
            tarifa = dict(t['claude-opus-5-5'], **mudanca)
            (self.raiz/'tarifas.json').write_text(json.dumps({'modelos': {'claude-opus-5-5': tarifa}}), encoding='utf-8')
            r = self.ler()
            self.assertIn(mod.AVISO_TARIFAS, r['avisos'])
            self.assertEqual(r['hoje']['usd'], .0006)

    def test_sem_cotacao_brl_nulo_usd_continua(self):
        self.arquivo('claude', [claude()])
        with patch.object(mod.gastos, 'cotacao', return_value=None):
            r = self.ler()
        self.assertIsNone(r['cotacao'])
        for dado in (r['hoje'], r['sete_dias'], r['sessoes'][0], r['dias'][-1]['total']):
            self.assertIsNone(dado['brl'])
            self.assertEqual(dado['usd'], .0006)

    def test_cotacao_compartilhada_data_e_desatualizada(self):
        self.arquivo('claude', [claude()])
        valor = {'usd_brl': 5.4, 'data_epoch': AGORA-200000, 'desatualizada': True, 'fonte': 'cache'}
        with patch.object(mod.gastos, 'cotacao', return_value=valor) as cot:
            self.assertEqual(self.ler()['cotacao'], valor)
        cot.assert_called_once_with(self.ctx, AGORA)

    def test_excecao_cotacao_nao_derruba_usd_inclusive_parcial(self):
        self.arquivo('claude', [claude(), claude(ident='m2', nome='modelo-novo')])
        with patch.object(mod.gastos, 'cotacao', side_effect=OSError('SEGREDO-PROMPT-Z')):
            r = self.ler()
        self.assertIsNone(r['cotacao'])
        self.assertEqual(r['hoje']['usd'], .0006)
        self.assertTrue(r['hoje']['parcial'])
        self.assertIsNone(r['hoje']['brl'])
        self.assertIsNone(r['sessoes'][0]['brl'])
        self.assertNotIn('SEGREDO-PROMPT-Z', json.dumps(r))

    def test_privacidade_json_serializado_todas_fontes(self):
        self.arquivo('claude', [claude(nome=r'C:\SEGREDO-PASTA-X')])
        self.arquivo('codex', [{'type': 'session_meta', 'payload': {'id': 'SEGREDO-ID-Y',
            'cwd': r'C:\SEGREDO-PASTA-X', 'prompt': 'SEGREDO-PROMPT-Z'}}, contexto(), codex()])
        self.arquivo('grok', {'prompt': 'SEGREDO-PROMPT-Z', 'turns': [{'endedAt': iso(AGORA-1),
            'primaryModelId': 'SEGREDO-ID-Y', 'costUsdTicks': 1, 'session_id': 'SEGREDO-ID-Y'}]}, nome='usage.json')
        with closing(sqlite3.connect(self.ctx['gastos_path'])) as db, db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('SEGREDO-ID-Y', 'SEGREDO-PROMPT-Z', '', '', AGORA-1, r'C:\SEGREDO-PASTA-X',
                        100, 10, 0, 0, 0, 1, None, 1))
        serializado = json.dumps(self.ler(), ensure_ascii=True, allow_nan=False)
        for segredo in ('SEGREDO-PASTA-X', 'SEGREDO-ID-Y', 'SEGREDO-PROMPT-Z', str(self.raiz)):
            self.assertNotIn(segredo, serializado)
        self.assertIn('outro modelo', serializado)

    def test_corrompido_contado_sem_derrubar_ou_vazar(self):
        p = self.arquivo('claude', [claude()])
        with p.open('a', encoding='utf-8') as f:
            f.write('SEGREDO-PROMPT-Z\n[]\n{"message":{"usage":null}}\n')
        self.arquivo('grok', [], nome='usage.json')
        self.arquivo('codex', [contexto(), {'type':'event_msg', 'payload':{'type':'token_count','info':[]}}])
        r = self.ler()
        self.assertEqual(r['registros_ignorados'], 5)
        self.assertEqual(r['hoje']['usd'], .0006)

    def test_zstd_corrompido_contado(self):
        p = self.arquivo('codex', [], nome='ruim.jsonl.zst')
        p.write_bytes(b'corrompido SEGREDO-PROMPT-Z')
        self.assertEqual(self.ler()['registros_ignorados'], 1)

    def test_cache_nao_rele_arquivo_inalterado_e_invalida_tamanho_mtime(self):
        p = self.arquivo('claude', [claude()])
        self.ler()
        with patch.object(mod, 'ler_arquivo', side_effect=AssertionError('releitura')):
            self.assertEqual(self.ler()['hoje']['usd'], .0006)
        p.write_text(json.dumps(claude(ident='nova'))+'\n'+json.dumps(claude())+'\n', encoding='utf-8')
        self.assertEqual(self.ler()['hoje']['usd'], .0012)

    def test_mtime_velho_e_eventos_fora_7dias_futuros_ignorados(self):
        self.arquivo('claude', [claude()], nome='antigo.jsonl', mtime=AGORA-9*86400)
        self.arquivo('codex', [contexto(), codex(AGORA-9*86400), codex(AGORA+1)])
        self.assertEqual(self.ler()['sete_dias']['tokens']['total'], 0)

    def test_hermes_mesmo_rateio_que_gastos_7dias_e_sem_mudar_banco(self):
        inicio = datetime.fromtimestamp(AGORA).replace(hour=0).timestamp()
        with closing(sqlite3.connect(self.ctx['gastos_path'])) as db, db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('SEGREDO-ID-Y', 'cli', '', '', inicio-1, 'gpt-6.1-sol', 300, 30, 60, 0, 9, 3, None, 9))
            db.executemany('INSERT INTO messages VALUES (?,?,?)',
                           [('SEGREDO-ID-Y', 'assistant', inicio-1),
                            ('SEGREDO-ID-Y', 'assistant', inicio+1), ('SEGREDO-ID-Y', 'assistant', inicio+2)])
        antes = Path(self.ctx['gastos_path']).read_bytes()
        r = self.ler()
        g = gastos_hermes.ler(self.ctx)['gastos_hermes']
        self.assertEqual(self.agente(r, 'hermes')['hoje']['usd'], g['hoje']['total']['cost_usd'])
        self.assertEqual(self.agente(r, 'hermes')['sete_dias']['usd'], 9)
        self.assertEqual(r['sessoes'][0]['tokens']['entrada'], 200)
        self.assertEqual(antes, Path(self.ctx['gastos_path']).read_bytes())

    def test_hermes_cache_considera_wal_e_dia(self):
        self.ler()
        with patch.object(mod.sqlite3, 'connect', side_effect=AssertionError('releitura')):
            self.ler()
        self.ctx['agora'] += 86400
        with patch.object(mod.sqlite3, 'connect', wraps=sqlite3.connect) as abrir:
            self.ler()
            self.assertEqual(abrir.call_count, 1)

    def test_hermes_tokens_arredondados_apenas_na_saida(self):
        inicio = datetime.fromtimestamp(AGORA).replace(hour=0).timestamp()
        with closing(sqlite3.connect(self.ctx['gastos_path'])) as db, db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('s', 'cli', '', '', inicio-86400, 'gpt-6.1-sol', 10, 2, 5, 0, 1, 3, None, 3))
            db.executemany('INSERT INTO messages VALUES (?,?,?)',
                [('s','assistant',inicio-1), ('s','assistant',inicio+1), ('s','assistant',inicio+3601)])
        r = self.ler()
        self.assertEqual(self.agente(r, 'hermes')['hoje']['tokens']['entrada'], 7)
        self.assertEqual(self.agente(r, 'hermes')['sete_dias']['tokens']['entrada'], 10)
        def inteiros(v):
            if isinstance(v, dict):
                if 'tokens' in v:
                    self.assertTrue(all(type(n) is int for n in v['tokens'].values()))
                for x in v.values():
                    inteiros(x)
            elif isinstance(v, list):
                for x in v:
                    inteiros(x)
        inteiros(r)
        self.assertEqual(self.ler(), r)  # arredondar não altera eventos no cache

    def test_hermes_indisponivel_nao_derruba_claude(self):
        self.ctx['gastos_path'] = self.raiz/'inexistente.db'
        self.arquivo('claude', [claude()])
        r = self.ler()
        self.assertEqual(self.agente(r, 'hermes')['estado'], 'sem leitura')
        self.assertEqual(r['hoje']['usd'], .0006)
        self.assertFalse(Path(self.ctx['gastos_path']).exists())

    def test_top_20_sessoes_hoje_mais_caras(self):
        for i in range(25):
            self.arquivo('grok', {'turns': [{'endedAt': iso(AGORA-1), 'costUsdTicks': (i+1)*10**10}]},
                         nome=f'{i}/usage.json')
        r = self.ler()
        self.assertEqual(r['sessoes_hoje'], 25)
        self.assertEqual(len(r['sessoes']), 20)
        self.assertEqual([s['usd'] for s in r['sessoes']], list(range(25, 5, -1)))

    def test_cursor_sem_leitura_e_contrato_de_sessao_exato(self):
        self.arquivo('claude', [claude()])
        r = self.ler()
        self.assertEqual(self.agente(r, 'cursor-agent')['estado'], 'sem leitura')
        self.assertIsNone(self.agente(r, 'cursor-agent')['hoje']['usd'])
        self.assertEqual(set(r['sessoes'][0]), {'agente', 'modelo', 'inicio_epoch', 'fim_epoch',
                                             'tokens', 'usd', 'brl', 'flags', 'parcial', 'eventos_sem_custo'})


class IntegracaoCustoTests(unittest.TestCase):
    def setUp(self):
        loader = SourceFileLoader('coletor_custo', str(RAIZ/'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        ambiente = patch.dict(os.environ)
        ambiente.start()
        self.addCleanup(ambiente.stop)
        for nome in tuple(os.environ):
            if nome.startswith('PAINEL_'):
                os.environ.pop(nome)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.fonte = Mock(return_value={'custo_agentes': {'estado': 'ok', 'dias': []}})
        for nome, retorno in (('gravar', None), ('montar', {'agentes': []})):
            guarda = patch.object(self.coletor, nome, return_value=retorno)
            guarda.start()
            self.addCleanup(guarda.stop)

    def passada(self, hora):
        return self.coletor.passada_viva(self.args, fontes={'custo_agentes': self.fonte},
                                        ctx={'agora': hora}, cache=self.cache)

    def test_optin_off_saida_serializada_identica_e_sem_importar(self):
        base = json.dumps(self.passada(1000), sort_keys=True)
        for valor in ('', '0', 'true', 'sim'):
            with self.subTest(valor=valor):
                os.environ['PAINEL_CUSTO'] = valor
                self.cache.clear()
                self.assertNotIn('custo_agentes', self.coletor.fontes_vivas(True))
                self.assertNotIn('custo_agentes', self.coletor.extras_ativos())
                self.assertEqual(json.dumps(self.passada(1000), sort_keys=True), base)
        self.fonte.assert_not_called()

    def test_registro_cadencia_300s(self):
        os.environ['PAINEL_CUSTO'] = '1'
        self.assertIn('custo_agentes', self.coletor.fontes_vivas(True))
        self.assertEqual(self.coletor.extras_ativos(), ('custo_agentes',))
        self.assertIn('custo_agentes', self.passada(1000))
        self.passada(1299)
        self.assertEqual(self.fonte.call_count, 1)
        self.passada(1300)
        self.assertEqual(self.fonte.call_count, 2)


if __name__ == '__main__':
    unittest.main()
