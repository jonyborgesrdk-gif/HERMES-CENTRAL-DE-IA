"""Contrato vivo com fontes falsas; sem rede, subprocessos ou sessões pessoais."""
import copy
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
import urllib.request

RAIZ = Path(__file__).resolve().parent.parent
loader = SourceFileLoader('coletor_vivo_tests', str(RAIZ / 'coletor'))
spec = spec_from_loader(loader.name, loader)
coletor = module_from_spec(spec)
loader.exec_module(coletor)
from vivo import montar_agentes, montar_contas

CAMINHO_CLAUDE = 'C:\\SEGREDO-PASTA-X\\projeto-alfa-zz'
CAMINHO_CODEX = '/raiz-yy/SEGREDO-PASTA-Y/projeto-beta-ww'


def setUpModule():
    for nome in ('run', 'Popen'):
        guarda = patch.object(subprocess, nome, side_effect=AssertionError('subprocesso real proibido'))
        guarda.start()
        unittest.addModuleCleanup(guarda.stop)
    guarda = patch.object(urllib.request, 'build_opener', side_effect=AssertionError('rede real proibida'))
    guarda.start()
    unittest.addModuleCleanup(guarda.stop)


def leituras():
    return {
        'eventos_hooks': {'sessoes': [{'session_id': 'claude-1', 'cwd': 'C:/exemplo',
            'transcript_path': 'sintetico', 'estado': 'esperando permissão', 'tool_name': 'Read'}]},
        'transcripts_claude': {'claude-1': {'modelo': 'modelo-claude', 'fala': 'Resposta sintética'}},
        'sessoes_codex': {'sessoes': [{'session_id': 'codex-1', 'pasta': 'C:/projeto',
            'modelo': 'modelo-codex', 'estado': 'trabalhando', 'sessao_5h': 17, 'semanal': 29,
            'reset_5h': 200000, 'reset_semanal': 300000, 'hora': 100000}]},
        'cota_claude': {'sessao_5h': 6, 'semanal': 42, 'hora': 100000},
        'maestri_wire': {'ligado': True, 'terminais': [{'id': 't-1', 'workspace': 'Time',
            'nome': 'Agente', 'tipo': 'custom', 'status': 'running', 'rodando': True,
            'atencao': False, 'descarregado': False, 'preview': ['feito', 'atividade', '']}]},
        'processos': {'codex.exe': 2},
        'servicos': {'servicos': [{'nome': 'Exemplo', 'estado': 'ok', 'detalhe': 'sintético', 'hora': 100000}]}}


class MontagemTests(unittest.TestCase):
    def test_permissao_modelo_cota(self):
        a = montar_agentes(leituras(), 100000, [])[0]
        self.assertEqual((a['ferramenta'], a['estado'], a['modelo']), ('claude', 'esperando', 'modelo-claude'))
        self.assertIsNone(a['atividade'])
        self.assertEqual((a['sessao_5h_pct'], a['semanal_pct']), (6, 42))

    def test_sessao_sobrevivente_sem_transcript_mantem_agente(self):
        r = leituras()
        r['transcripts_claude']['claude-1'] = None
        agentes = montar_agentes(r, 100000, [])
        self.assertEqual(agentes[0]['ferramenta'], 'claude')
        self.assertEqual(agentes[0]['nome'], 'Claude · ude1')
        self.assertIsNone(agentes[0]['modelo'])
        self.assertEqual(agentes[0]['estado'], 'esperando')

    def test_codex_usado_proprio(self):
        a = montar_agentes(leituras(), 100000, [])[1]
        self.assertEqual((a['sessao_5h_pct'], a['semanal_pct']), (17, 29))
        self.assertEqual(a['lido_em_epoch'], 100000)

    def test_mesma_pasta_duas_sessoes(self):
        r = leituras()
        r['eventos_hooks']['sessoes'].append(dict(r['eventos_hooks']['sessoes'][0], session_id='claude-2'))
        a = montar_agentes(r, 100000, [])
        self.assertEqual([x['nome'] for x in a[:2]], ['Claude · ude1', 'Claude · ude2'])

    def test_overrides_por_pasta_e_nome(self):
        r = montar_agentes(leituras(), 100000, [{'pasta': 'c:\\EXEMPLO', 'nome': 'Revisor'},
            {'nome': 'Codex · dex1', 'conta': 'Conta sintética'}])
        self.assertEqual(r[0]['nome'], 'Revisor')
        self.assertEqual(r[1]['conta'], 'Conta sintética')

    def test_override_de_pasta_respeita_ferramenta_e_modelo_lido(self):
        r = leituras()
        r['sessoes_codex']['sessoes'][0]['pasta'] = 'C:/exemplo'
        a = montar_agentes(r, 100000, [{'pasta': 'C:/exemplo', 'ferramenta': 'claude',
            'nome': 'Revisor', 'modelo': 'modelo-fallback'}])
        self.assertEqual((a[0]['nome'], a[0]['modelo']), ('Revisor', 'modelo-claude'))
        self.assertEqual(a[1]['nome'], 'Codex · dex1')
        self.assertEqual(a[1]['modelo'], 'modelo-codex')
        r['transcripts_claude']['claude-1']['modelo'] = None
        self.assertEqual(montar_agentes(r, 100000, [{'pasta': 'C:/exemplo',
            'ferramenta': 'claude', 'modelo': 'fallback'}])[0]['modelo'], 'fallback')

    def test_contas_claude_plano_email_usado_reset(self):
        r = leituras()
        r['cota_claude'].update(plano='Max', email='conta@example.com', reset_5h='Oct 27, 1am')
        antes = copy.deepcopy(r)
        contas = montar_contas(r, 100000, montar_agentes(r, 100000, []))
        self.assertTrue(contas['consultadas'])
        c = contas['lista'][0]
        self.assertEqual((c['titulo'], c['email']), ('Claude · Max', 'conta@example.com'))
        self.assertEqual([b['usado_pct'] for b in c['barras']], [6, 42])
        self.assertEqual(c['barras'][0]['renova_texto'], 'Oct 27, 1am')
        self.assertIsNone(c['barras'][0]['renova_epoch'])
        self.assertEqual(r, antes)

    def test_contas_codex_agrupa_e_usa_ultima_leitura(self):
        r = leituras()
        s = r['sessoes_codex']['sessoes'][0]
        s['limit_id'] = 'conta-a'
        r['sessoes_codex']['sessoes'] += [dict(s, session_id='outro', hora=100100, sessao_5h=33),
            dict(s, session_id='terceiro', limit_id='conta-b', hora=100050, sessao_5h=70)]
        codex = [c for c in montar_contas(r, 100200)['lista'] if c['ferramenta'] == 'codex']
        self.assertEqual(len(codex), 2)
        self.assertEqual([c['barras'][0]['usado_pct'] for c in codex], [33, 70])
        self.assertEqual(codex[0]['barras'][0]['renova_epoch'], 200000)
        self.assertEqual(codex[0]['barras'][0]['origem'], 'sessao')

    def test_contas_codex_sem_identidade_um_card_e_sem_dado(self):
        r = leituras()
        s = r['sessoes_codex']['sessoes'][0]
        r['sessoes_codex']['sessoes'].append(dict(s, session_id='outro', hora=100100, sessao_5h=66))
        codex = [c for c in montar_contas(r, 100200)['lista'] if c['ferramenta'] == 'codex']
        self.assertEqual(len(codex), 1)
        self.assertEqual((codex[0]['titulo'], codex[0]['barras'][0]['usado_pct']), ('Codex', 66))
        contas = montar_contas({}, 100000, disponiveis={'sessoes_codex', 'cota_claude'})
        self.assertTrue(contas['consultadas'])
        self.assertTrue(all(c['situacao'] == 'sem dado' for c in contas['lista']))
        self.assertTrue(all(b['usado_pct'] is None for c in contas['lista'] for b in c['barras']))
        self.assertFalse(montar_contas({}, 100000, disponiveis=set())['consultadas'])

    def test_pureza(self):
        r = leituras()
        antes = copy.deepcopy(r)
        montar_agentes(r, 100000, [])
        self.assertEqual(r, antes)

    def test_dedup_wire_so_por_pasta_e_tipo(self):
        r = leituras()
        t = r['maestri_wire']['terminais'][0]
        t.update(tipo='codex', workingDirectory='c:\\PROJETO')
        self.assertEqual(len(montar_agentes(r, 100000, [])), 2)
        t.pop('workingDirectory')
        self.assertEqual(len(montar_agentes(r, 100000, [])), 3)
        t.update(tipo='custom', workingDirectory='C:/projeto')
        self.assertEqual(len(montar_agentes(r, 100000, [])), 3)

    def test_dedup_com_fonte_wire_real_http_falso(self):
        from lib.fontes import maestri_wire
        with tempfile.TemporaryDirectory() as pasta:
            (Path(pasta) / 'wire.conf').write_text('WIRE_TOKEN=token-sintetico', encoding='utf-8')
            def http(url, **kwargs):
                if url.endswith('/workspaces'):
                    return {'workspaces': [{'id': 'w', 'name': 'Time'}]}
                if url.endswith('/settings'):
                    return {'workingDirectory': 'c:\\PROJETO'}
                return {'items': [{'type': 'terminal', 'terminal': {'id': 't',
                    'agentType': 'codex', 'isRunning': True, 'status': 'running', 'name': 'Codex'}}]}
            r = leituras()
            r['maestri_wire'] = maestri_wire.ler({'dir': pasta, 'agora': 100000, 'http_get': http})
            self.assertEqual(r['maestri_wire']['terminais'][0]['pasta'], 'c:\\PROJETO')
            self.assertEqual(len(montar_agentes(r, 100000, [])), 2)

    def test_wire_preview_e_prioridades(self):
        r = leituras()
        t = r['maestri_wire']['terminais'][0]
        a = montar_agentes(r, 100000, [])[-1]
        self.assertEqual((a['atividade'], a['ultima_fala'], a['pasta']), (None, None, None))
        t['atencao'] = True
        self.assertEqual(montar_agentes(r, 100000, [])[-1]['estado'], 'esperando')
        t['descarregado'] = True
        self.assertEqual(montar_agentes(r, 100000, [])[-1]['estado'], 'parado')

    def test_wire_sem_tela_nao_ocioso_inventado(self):
        r = leituras()
        r['maestri_wire']['terminais'][0]['preview'] = []
        a = montar_agentes(r, 100000, [])[-1]
        self.assertEqual((a['estado'], a['atividade']), ('trabalhando', None))

    def _terminal_sintetico(self):
        r = leituras()
        r['maestri_wire']['terminais'][0].update(workingDirectory='C:/pasta-sintetica-xyz',
            pasta='C:/pasta-sintetica-xyz', preview=['linha-sintetica-um', 'linha-sintetica-dois'])
        return r

    def test_maestri_publica_so_nome_tipo_e_estado(self):
        a = montar_agentes(self._terminal_sintetico(), 100000, [])[-1]
        self.assertEqual((a['nome'], a['ferramenta_nome'], a['estado'], a['fonte']),
                         ('Time/Agente', 'Maestri · custom', 'trabalhando', 'maestri_wire'))
        self.assertIsNone(a['pasta'])
        self.assertIsNone(a['atividade'])
        self.assertIsNone(a['ultima_fala'])
        self.assertNotIn('sintetica', json.dumps(a))

    def test_maestri_override_por_pasta_sem_publicar_a_pasta(self):
        overrides = [{'pasta': 'C:/pasta-sintetica-xyz', 'ferramenta': 'maestri', 'esforco': 'alto'}]
        a = montar_agentes(self._terminal_sintetico(), 100000, overrides)[-1]
        self.assertEqual(a['esforco'], 'alto')
        self.assertIsNone(a['pasta'])

    def test_claude_e_codex_publicam_so_nome_tipo_e_estado(self):
        r = leituras()
        r['eventos_hooks']['sessoes'][0].update(cwd=CAMINHO_CLAUDE, tool_name='FERRAMENTA-SECRETA')
        r['transcripts_claude']['claude-1'].update(pasta=CAMINHO_CLAUDE, fala='FALA-SECRETA')
        r['sessoes_codex']['sessoes'][0]['pasta'] = CAMINHO_CODEX
        agentes = montar_agentes(r, 100000, [])
        claude = next(a for a in agentes if a['fonte'] == 'eventos_hooks')
        codex = next(a for a in agentes if a['fonte'] == 'sessoes_codex')
        self.assertEqual((claude['nome'], claude['ferramenta_nome'], claude['estado']),
                         ('Claude · ude1', 'Claude Code', 'esperando'))
        self.assertEqual((codex['nome'], codex['ferramenta_nome'], codex['estado']),
                         ('Codex · dex1', 'Codex', 'trabalhando'))
        for a in (claude, codex):
            self.assertIsNone(a['pasta'])
            self.assertIsNone(a['atividade'])
            self.assertIsNone(a['ultima_fala'])
        texto = json.dumps(agentes)
        for trecho in ('SEGREDO', 'projeto-alfa', 'projeto-beta', 'raiz-yy', 'FALA-SECRETA',
                       'FERRAMENTA-SECRETA', 'pede permiss'):
            self.assertNotIn(trecho, texto)
        self.assertEqual(claude['modelo'], 'modelo-claude')
        self.assertEqual((claude['sessao_5h_pct'], claude['semanal_pct']), (6, 42))
        self.assertEqual(codex['modelo'], 'modelo-codex')
        self.assertEqual((codex['sessao_5h_pct'], codex['semanal_pct']), (17, 29))

    def test_nome_padrao_vem_do_identificador_e_nao_da_pasta(self):
        nomes = []
        for pasta_claude, pasta_codex in (('C:/exemplo', 'C:/projeto'), (CAMINHO_CLAUDE, CAMINHO_CODEX)):
            r = leituras()
            r['eventos_hooks']['sessoes'][0]['cwd'] = pasta_claude
            r['sessoes_codex']['sessoes'][0]['pasta'] = pasta_codex
            agentes = montar_agentes(r, 100000, [])
            nomes.append([next(a for a in agentes if a['fonte'] == 'eventos_hooks')['nome'],
                          next(a for a in agentes if a['fonte'] == 'sessoes_codex')['nome']])
        self.assertEqual(nomes, [['Claude · ude1', 'Codex · dex1'], ['Claude · ude1', 'Codex · dex1']])

    def test_nome_padrao_casos_limite(self):
        casos = ((None, 'Codex'), ('', 'Codex'), ('----', 'Codex'), ('C:\\SEGREDO\\x', 'Codex'),
                 ('/home/x/y', 'Codex'), ('a.b', 'Codex'), ('ab', 'Codex · ab'), (123, 'Codex · 123'),
                 ('0199a3f2-7c1e-7b2a-9d4e-1f2a3b4c5d6e', 'Codex · 5d6e'))
        for session_id, esperado in casos:
            with self.subTest(session_id=session_id):
                r = leituras()
                r['sessoes_codex']['sessoes'][0]['session_id'] = session_id
                a = next(x for x in montar_agentes(r, 100000, []) if x['fonte'] == 'sessoes_codex')
                self.assertEqual(a['nome'], esperado)
                for proibido in ('/', '\\', ':'):
                    self.assertNotIn(proibido, a['nome'])

    def test_nomes_iguais_ganham_sufixo_numerado(self):
        r = leituras()
        s = r['eventos_hooks']['sessoes'][0]
        r['eventos_hooks']['sessoes'] = [dict(s, session_id='x-ude1'), dict(s, session_id='y-ude1')]
        agentes = montar_agentes(r, 100000, [])
        self.assertEqual([a['nome'] for a in agentes if a['fonte'] == 'eventos_hooks'],
                         ['Claude · ude1', 'Claude · ude1 (2)'])

    def test_override_por_pasta_de_claude_e_codex_sem_publicar_a_pasta(self):
        agentes = montar_agentes(leituras(), 100000, [{'pasta': 'c:\\EXEMPLO', 'nome': 'Revisor'},
            {'pasta': 'C:/projeto', 'ferramenta': 'codex', 'nome': 'Painel', 'esforco': 'alto'}])
        claude = next(a for a in agentes if a['fonte'] == 'eventos_hooks')
        codex = next(a for a in agentes if a['fonte'] == 'sessoes_codex')
        self.assertEqual((claude['nome'], codex['nome']), ('Revisor', 'Painel'))
        self.assertEqual(codex['esforco'], 'alto')
        self.assertIsNone(claude['pasta'])
        self.assertIsNone(codex['pasta'])
        texto = json.dumps([claude, codex]).lower()
        self.assertNotIn('exemplo', texto)
        self.assertNotIn('projeto', texto)

    def test_fonte_wire_real_http_falso_nao_publica_pasta_nem_tela(self):
        from lib.fontes import maestri_wire
        with tempfile.TemporaryDirectory() as pasta:
            (Path(pasta) / 'wire.conf').write_text('WIRE_TOKEN=token-sintetico', encoding='utf-8')
            def http(url, **kwargs):
                if url.endswith('/workspaces'):
                    return {'workspaces': [{'id': 'w', 'name': 'Time'}]}
                if url.endswith('/settings'):
                    return {'workingDirectory': 'C:/pasta-sintetica-xyz'}
                return {'items': [{'kind': 'terminal', 'terminal': {'id': 't', 'name': 'Agente',
                    'agentType': 'custom', 'status': 'running', 'isRunning': True,
                    'preview': ['linha-sintetica-um', 'linha-sintetica-dois']}}]}
            r = leituras()
            r['maestri_wire'] = maestri_wire.ler({'dir': pasta, 'agora': 100000, 'http_get': http})
            self.assertEqual(r['maestri_wire']['terminais'][0]['pasta'], 'C:/pasta-sintetica-xyz')
            self.assertNotIn('linha-sintetica', json.dumps(r['maestri_wire']))
            montado = json.dumps(montar_agentes(r, 100000, []))
            self.assertNotIn('linha-sintetica', montado)
            self.assertNotIn('pasta-sintetica', montado)

    def test_ausencia_e_mascaramento(self):
        r = leituras()
        r['transcripts_claude']['claude-1']['fala'] = 'sk-' + 'a' * 40
        r['eventos_hooks']['sessoes'][0]['estado'] = 'ocioso'
        a = montar_agentes(r, 100000, [])[0]
        self.assertIsNone(a['atividade'])
        self.assertNotIn('sk-' + 'a' * 40, json.dumps(a))
        self.assertIsNone(a['contexto_pct'])
        self.assertEqual(montar_agentes({}, 100000, []), [])


class PassadaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=False, uma_vez=True, intervalo=2,
            intervalo_prs=180, intervalo_sonda=300, telas=None, saida=str(self.pasta))
        self.calls = {}
        self.r = leituras()
        self.fontes = {}
        for nome in self.r:
            self.fontes[nome] = self.fonte(nome)
        p = patch.object(coletor, 'ler_quadro', return_value=(coletor.comum.quadro_vazio(), None))
        p.start()
        self.addCleanup(p.stop)

    def fonte(self, nome):
        def ler(ctx):
            self.calls[nome] = self.calls.get(nome, 0) + 1
            if nome == 'transcripts_claude':
                return copy.deepcopy(self.r[nome]['claude-1'])
            return copy.deepcopy(self.r[nome])
        return ler

    def passada(self, **kwargs):
        return coletor.passada_viva(self.args, fontes=self.fontes,
            ctx={'dir': self.pasta, 'agora': kwargs.pop('agora', 100000)}, **kwargs)

    def test_js_mesmo_contrato_do_modo_antigo(self):
        dados = self.passada()
        js = (self.pasta / 'dados.js').read_text(encoding='utf-8')
        prefixo = 'window.__painelDados && window.__painelDados('
        self.assertTrue(js.startswith(prefixo) and js.rstrip().endswith(');'))
        self.assertEqual(json.loads(js[len(prefixo):js.rstrip().rfind(');')]), dados)
        self.assertEqual(json.loads((self.pasta / 'dados.json').read_text(encoding='utf-8')), dados)
        self.assertTrue(js.isascii())
        self.assertEqual(sorted(p.name for p in self.pasta.iterdir()), ['dados.js', 'dados.json'])
        self.assertEqual(dados['origem'], 'vivo')
        self.assertEqual(dados['processos'], {'codex.exe': 2})
        self.assertIsInstance(dados['servicos'], list)
        self.assertTrue(all(f['ok'] for f in dados['fontes'].values()))

    def test_arquivos_gravados_nao_levam_pasta_nem_tela_do_maestri(self):
        self.r['maestri_wire']['terminais'][0].update(workingDirectory='C:/pasta-sintetica-xyz',
            pasta='C:/pasta-sintetica-xyz', preview=['linha-sintetica-um', 'linha-sintetica-dois'])
        dados = self.passada()
        for nome in ('dados.json', 'dados.js'):
            texto = (self.pasta / nome).read_text(encoding='utf-8')
            self.assertNotIn('pasta-sintetica', texto)
            self.assertNotIn('linha-sintetica', texto)
        a = next(x for x in dados['agentes'] if x['fonte'] == 'maestri_wire')
        self.assertEqual((a['nome'], a['ferramenta_nome'], a['estado']),
                         ('Time/Agente', 'Maestri · custom', 'trabalhando'))

    def test_arquivos_gravados_nao_levam_caminho_nem_texto_de_sessao(self):
        sessao = self.r['eventos_hooks']['sessoes'][0]
        sessao.update(cwd=CAMINHO_CLAUDE, transcript_path='C:\\TRANSCRIPT-SECRETO\\t.jsonl',
                      tool_name='FERRAMENTA-SECRETA')
        self.r['eventos_hooks']['sessoes'].append(dict(sessao, session_id='claude-2', estado='trabalhando',
                                                       cwd=CAMINHO_CLAUDE + '\\sub-kk'))
        self.r['transcripts_claude']['claude-1'].update(
            pasta=CAMINHO_CLAUDE, fala='FALA-SECRETA em ' + CAMINHO_CLAUDE + '\\a.py')
        self.r['sessoes_codex']['sessoes'][0]['pasta'] = CAMINHO_CODEX
        self.r['maestri_wire']['terminais'].append({'id': 't-2', 'workspace': 'Time', 'nome': 'Duplicado',
            'tipo': 'codex', 'status': 'running', 'rodando': True, 'atencao': False,
            'descarregado': False, 'pasta': CAMINHO_CODEX})
        dados = self.passada()
        proibidos = ['SEGREDO', 'PASTA-X', 'PASTA-Y', 'projeto-alfa-zz', 'projeto-beta-ww', 'raiz-yy',
                     'sub-kk', 'TRANSCRIPT-SECRETO', 'FALA-SECRETA', 'FERRAMENTA-SECRETA', 'pede permiss',
                     'Duplicado']
        for caminho in (CAMINHO_CLAUDE, CAMINHO_CODEX):
            proibidos += [caminho, caminho.replace('\\', '/'), json.dumps(caminho)[1:-1]]
        for nome in ('dados.json', 'dados.js'):
            texto = (self.pasta / nome).read_text(encoding='utf-8')
            for trecho in proibidos:
                self.assertNotIn(trecho.lower(), texto.lower(), (nome, trecho))
        self.assertEqual([a['nome'] for a in dados['agentes']],
                         ['Claude · ude1', 'Claude · ude2', 'Codex · dex1', 'Time/Agente'])
        self.assertEqual([(a['ferramenta_nome'], a['estado']) for a in dados['agentes'][:3]],
                         [('Claude Code', 'esperando'), ('Claude Code', 'trabalhando'), ('Codex', 'trabalhando')])
        for a in dados['agentes']:
            self.assertIsNone(a['pasta'])
            self.assertIsNone(a['atividade'])
            self.assertIsNone(a['ultima_fala'])

    def test_html_chaves_em_escopos_reais(self):
        dados = self.passada()
        html = (RAIZ / 'publico' / 'classico.html').read_text(encoding='utf-8')
        # v é agente nestas funções, mas também é virador em outros escopos.
        for nome in ('celAgente', 'celEstado', 'celAgora', 'celMissao', 'celPrs'):
            m = re.search(r'function ' + nome + r'\(v\)\s*\{(.*?)(?=\nfunction |\nvar )', html, re.S)
            self.assertIsNotNone(m, nome)
            self.assertLessEqual(set(re.findall(r'\bv\.(\w+)', m[1])), set(dados['agentes'][0]))
        callback = html.split('window.__painelDados = function (d) {', 1)[1].split('\n};', 1)[0]
        self.assertLessEqual(set(re.findall(r'\bd\.(\w+)', callback)), set(dados))
        # Literais como "dados.js" não são acessos ao objeto dados.
        codigo = '\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.S))
        codigo = re.sub(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|/\*.*?\*/|//[^\n]*''', '', codigo, flags=re.S)
        self.assertLessEqual(set(re.findall(r'\bdados\.(\w+)', codigo)), set(dados))
        # A forma do registro antigo é produzida pelo mesmo montar(), não por cópia paralela.
        self.assertFalse(dados['sonda']['ligada'])

    def test_falha_fonte_isolada(self):
        def falhar(ctx):
            raise RuntimeError('segredo que não deve vazar')
        self.fontes['maestri_wire'] = falhar
        dados = self.passada()
        self.assertEqual(len(dados['agentes']), 2)
        self.assertFalse(dados['fontes']['maestri_wire']['ok'])
        self.assertNotIn('segredo', dados['fontes']['maestri_wire']['erro'])

    def test_erro_retornado_e_transcript(self):
        self.r['cota_claude']['erro'] = 'cache indisponível'
        self.r['transcripts_claude']['claude-1'] = {'erro': 'ausente'}
        dados = self.passada()
        self.assertFalse(dados['fontes']['cota_claude']['ok'])
        self.assertFalse(dados['fontes']['transcripts_claude']['ok'])
        self.assertEqual(len(dados['agentes']), 3)

    def test_sessao_sem_transcript_nao_e_erro_da_fonte(self):
        self.fontes['transcripts_claude'] = lambda ctx: None
        dados = self.passada()
        claude = next(a for a in dados['agentes'] if a['ferramenta'] == 'claude')
        self.assertEqual(claude['nome'], 'Claude · ude1')
        self.assertIsNone(claude['modelo'])
        self.assertTrue(dados['fontes']['transcripts_claude']['ok'])
        self.assertIsNone(dados['fontes']['transcripts_claude']['erro'])

    def test_erro_transcript_preserva_snapshot_por_sessao(self):
        cache = {}
        self.passada(cache=cache)
        self.r['transcripts_claude']['claude-1'] = {'erro': 'arquivo sem leitura'}
        dados = self.passada(cache=cache, agora=100002)
        claude = next(a for a in dados['agentes'] if a['ferramenta'] == 'claude')
        self.assertEqual(claude['modelo'], 'modelo-claude')
        self.assertIsNone(claude['ultima_fala'])
        self.assertFalse(dados['fontes']['transcripts_claude']['ok'])
        self.assertEqual(dados['fontes']['transcripts_claude']['erro'], 'transcript sem leitura')
        self.assertEqual(cache['transcripts_claude']['dados']['claude-1']['modelo'], 'modelo-claude')

    def test_so_none_e_ausencia_normal_outro_tipo_e_invalido(self):
        self.fontes['transcripts_claude'] = lambda ctx: 42
        dados = self.passada()
        self.assertFalse(dados['fontes']['transcripts_claude']['ok'])
        self.assertIn('ValueError', dados['fontes']['transcripts_claude']['erro'])

    def test_cadencias(self):
        cache = {}
        self.passada(cache=cache)
        self.passada(cache=cache, agora=100002)
        self.assertEqual(self.calls['eventos_hooks'], 2)
        self.assertEqual(self.calls['servicos'], 1)
        self.assertEqual(self.calls['processos'], 1)
        self.assertEqual(self.calls['cota_claude'], 1)
        self.passada(cache=cache, agora=100005)
        self.assertEqual(self.calls['processos'], 2)
        self.passada(cache=cache, agora=100010)
        self.assertEqual(self.calls['servicos'], 2)
        self.passada(cache=cache, agora=100300)
        self.assertEqual(self.calls['cota_claude'], 2)

    def test_falha_mantem_snapshot_anterior(self):
        cache = {}
        self.passada(cache=cache)
        def falha(ctx):
            raise TimeoutError
        self.fontes['processos'] = falha
        d = self.passada(cache=cache, agora=100005)
        self.assertEqual(d['processos'], {'codex.exe': 2})
        self.assertFalse(d['fontes']['processos']['ok'])

    def test_erro_retornado_mantem_snapshot_e_marca_fonte(self):
        cache = {}
        antes = self.passada(cache=cache)
        self.fontes['maestri_wire'] = lambda ctx: {'erro': 'Wire fora'}
        d = self.passada(cache=cache, agora=100002)
        self.assertEqual([a['nome'] for a in d['agentes']], [a['nome'] for a in antes['agentes']])
        self.assertEqual(d['fontes']['maestri_wire']['erro'], 'Wire fora')
        self.assertFalse(d['fontes']['maestri_wire']['ok'])
        self.assertGreaterEqual(len(d['contas']['lista']), 1)

    def test_cotas_codex_nao_misturadas_por_pasta(self):
        a = self.r['sessoes_codex']['sessoes'][0]
        self.r['sessoes_codex']['sessoes'].append(dict(a, session_id='codex-2', sessao_5h=80))
        d = self.passada()
        codex = [a for a in d['agentes'] if a['ferramenta'] == 'codex']
        self.assertNotEqual(codex[0]['grupo_cota'], codex[1]['grupo_cota'])
        self.assertEqual([d['cotas'][a['grupo_cota']]['sessao_5h_pct'] for a in codex], [17, 80])

    def test_main_vivo_nao_inicia_sonda_tmux_ou_contas(self):
        args = ['coletor', '--vivo', '--com-sonda', '--uma-vez', '--sem-gh', '--saida', str(self.pasta)]
        with patch('sys.argv', args), patch.object(coletor.comum, 'ARQ_AGENTES', self.pasta / 'ausente.json'), \
             patch.object(coletor, 'fontes_vivas', return_value=self.fontes), \
             patch.object(coletor, 'FioDaSonda', side_effect=AssertionError('sonda proibida')), \
             patch.object(coletor, 'Tmux', side_effect=AssertionError('tmux proibido')), \
             patch.object(coletor.modulo_contas, 'Contas', side_effect=AssertionError('contas legadas proibidas')), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(coletor.main(), 0)
        d = json.loads((self.pasta / 'dados.json').read_text(encoding='utf-8'))
        self.assertEqual(d['intervalo_s'], 2)
        self.assertEqual(d['origem'], 'vivo')

    def test_flags_mutuamente_exclusivas(self):
        with patch('sys.argv', ['coletor', '--vivo', '--telas', str(self.pasta)]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as e:
                coletor.main()
        self.assertEqual(e.exception.code, 2)
