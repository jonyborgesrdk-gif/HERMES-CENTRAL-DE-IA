"""E28: atribuição conservadora, arquivos limitados e árvores falsas, sem ETW real."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.fontes import rede_sessao as r
from lib.fontes import presenca_processos as p

AGORA = 1700000100.
AMBIENTE = {'USERPROFILE': r'C:\Users\teste', 'LOCALAPPDATA': r'C:\Users\teste\AppData\Local'}
TABELA = p.tabela_resolvida(AMBIENTE)


def criado(seg):
    return int((1700000000 + seg + 11644473600) * 10 ** 7)


def processo(pid, ppid=0, seg=0, agente=None):
    caminhos = {'claude': r'C:\Users\teste\.local\bin\claude.exe',
                'codex': r'C:\Users\teste\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe',
                'grok': r'C:\Users\teste\.grok\bin\grok.exe',
                'hermes': r'C:\Users\teste\AppData\Local\hermes\bin\hermes.exe',
                'cursor-agent': r'C:\Users\teste\AppData\Local\cursor-agent\versions\1\node.exe'}
    return dict(pid=pid, ppid=ppid, criado=criado(seg), caminho=caminhos.get(agente, r'C:\bin\worker.exe'),
                nome='worker.exe')


def documento():
    return dict(schema=1, estado='ativo', desde=1700000000., gerado_em=AGORA,
                pids=[dict(pid=3, criado=criado(2), janela=[10, 20], total=[100, 200])],
                fluxos=[dict(pid=3, criado=criado(2), proto='tcp', local_porta=1234,
                    remoto='203.0.113.10', remoto_porta=443, janela=[10, 20], total=[100, 200])],
                nao_atribuidos=r.vazio(), fluxos_omitidos=0, pids_omitidos=0)


class RedeSessao(unittest.TestCase):
    def setUp(self):
        self.processos = {p['pid']: p for p in [processo(1, agente='claude'), processo(2, 1, 1), processo(3, 2, 2)]}
        self.d = documento()

    def montar(self):
        return r.montar(self.d, self.processos, TABELA, AGORA)

    def test_filho_neto_bytes_basename_e_identificador_nulo(self):
        self.d['pids'].append(dict(pid=2, criado=criado(1), janela=[3, 4], total=[30, 40]))
        s = self.montar()['sessoes'][0]
        self.assertEqual((s['agente'], s['pid_raiz'], s['processos'], s['sessao']), ('claude', 1, 3, None))
        self.assertEqual(s['inicio_epoch'], 1700000000.)
        self.assertEqual(s['janela'], [13, 24])
        self.assertEqual(s['total'], [130, 240])
        self.assertEqual(s['conexoes'][0]['processo'], 'worker.exe')
        self.assertNotIn('caminho', json.dumps(s))

    def test_ciclo_reuso_pid_raiz_morta_e_criacao_ilegivel(self):
        for caso in ('ciclo', 'pid_reutilizado', 'pai_reutilizado', 'raiz_morta', 'sem_criacao'):
            with self.subTest(caso=caso):
                self.setUp()
                if caso == 'ciclo': self.processos[1]['ppid'] = 3; self.processos[1]['criado'] = criado(2); self.processos[2]['criado'] = criado(2)
                if caso == 'pid_reutilizado': self.processos[3]['criado'] = criado(50)
                if caso == 'pai_reutilizado': self.processos[2]['criado'] = criado(50)
                if caso == 'raiz_morta': del self.processos[1]
                if caso == 'sem_criacao': self.processos[3]['criado'] = None
                saida = self.montar()
                self.assertEqual(saida['sessoes'], [])
                identidade_valida = caso not in ('pid_reutilizado', 'sem_criacao')
                self.assertEqual(saida['outros']['total'], [0, 0] if identidade_valida else [100, 200])
                self.assertEqual(saida['outros']['nao_atribuidos']['total'],
                                 [100, 200] if identidade_valida else [0, 0])

    def test_cadeia_limitada_e_heranca_de_produto_nao_inventada(self):
        self.processos = {i: processo(i, i-1, i) for i in range(1, 70)}
        self.processos[1] = processo(1, agente='claude')
        self.assertIsNone(r.raiz(69, self.processos, TABELA))
        for agente in r.AGENTES.values():
            self.assertEqual(r.raiz(1, {1: processo(1, agente=agente)}, TABELA), (agente, 1, criado(0)))

    def test_ordenacao_tetos_de_sessoes_conexoes_e_contagens(self):
        self.processos[4] = processo(4, seg=1, agente='codex')
        self.d['pids'].append(dict(pid=4, criado=criado(1), janela=[100, 200], total=[1000, 2000]))
        self.d['fluxos'] = [copy.deepcopy(self.d['fluxos'][0]) for _ in range(3)]
        for porta, fluxo in enumerate(self.d['fluxos'], 1234): fluxo['local_porta'] = porta
        with patch.object(r, 'MAX_SESSOES', 1):
            saida = self.montar()
        self.assertEqual(saida['sessoes'][0]['agente'], 'codex')
        self.assertEqual(saida['sessoes_omitidas'], 1)
        with patch.object(r, 'MAX_CONEXOES_SESSAO', 1):
            saida = self.montar()
        s = next(s for s in saida['sessoes'] if s['agente'] == 'claude')
        self.assertEqual((len(s['conexoes']), s['conexoes_omitidas'], s['total']), (1, 2, [100, 200]))
        with patch.object(r, 'MAX_CONEXOES', 0):
            self.assertEqual(self.montar()['sessoes'][1]['conexoes_omitidas'], 3)

    def test_arquivo_ausente_velho_corrompido_gigante_e_formato_hostil(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / r.ARQUIVO
            listar = lambda _: list(self.processos.values())
            ctx = dict(rede_sessao=caminho, agora=AGORA, ambiente=AMBIENTE, listar_processos=listar)
            self.assertEqual(r.ler(ctx)['rede_sessao']['estado'], 'sem captura detalhada')
            self.assertIn('--detalhe', r.ler(ctx)['rede_sessao']['instrucao'])
            self.d['gerado_em'] = AGORA - 31
            caminho.write_text(json.dumps(self.d), encoding='utf-8')
            self.assertEqual(r.ler(ctx)['rede_sessao']['estado'], 'parado')
            self.assertEqual(r.ler(ctx)['rede_sessao']['idade_s'], 31)
            for bruto in (b'{', b'[]', b'x' * (r.MAX_ARQUIVO+1), b'{"schema":1,"gerado_em":NaN}'):
                caminho.write_bytes(bruto)
                self.assertEqual(r.ler(ctx), {'erro': r.ERRO})
            for campo, valor in [('nao_atribuidos', []), ('fluxos', [None]), ('pids', [None]),
                                 ('gerado_em', float('inf')), ('pids', self.d['pids']*2)]:
                d = dict(self.d, **{campo: valor})
                caminho.write_text(json.dumps(d), encoding='utf-8')
                self.assertEqual(r.ler(ctx), {'erro': r.ERRO})

    def test_limites_de_entrada_e_totais_sem_pid_confiavel(self):
        self.d['nao_atribuidos'] = dict(janela=[7, 8], total=[70, 80])
        self.d['pids'][0]['criado'] = None
        self.d['fluxos'][0]['criado'] = None
        self.assertEqual(self.montar()['outros']['total'], [170, 280])
        with patch.object(r, 'MAX_PIDS', 0), self.assertRaises(ValueError): self.montar()
        with patch.object(r, 'MAX_FLUXOS', 0), self.assertRaises(ValueError): self.montar()
        with patch.object(r, 'MAX_PROCESSOS', 1):
            self.assertEqual(len(r.snapshot(lambda _: list(self.processos.values()))), 1)

    def test_e29_c6c_contadores_disjuntos_expirados_e_raiz_memorizada(self):
        self.processos[4] = processo(4, seg=1)
        self.d['pids'].extend([
            dict(pid=4, criado=criado(1), janela=[1, 2], total=[10, 20]),
            dict(pid=5, criado=None, janela=[3, 4], total=[30, 40])])
        self.d['nao_atribuidos'] = dict(janela=[7, 8], total=[70, 80])
        self.d['expirados'] = dict(janela=[0, 0], total=[9, 10], fluxos_total=[9, 10])
        with patch.object(r, 'raiz', wraps=r.raiz) as resolver:
            saida = self.montar()
        self.assertEqual(resolver.call_count, len(self.processos))
        self.assertEqual(saida['sessoes'][0]['total'], [100, 200])
        self.assertEqual(saida['outros']['total'], [109, 130])
        self.assertEqual(saida['outros']['janela'], [10, 12])
        self.assertEqual(saida['outros']['nao_atribuidos']['total'], [10, 20])
        self.assertEqual(saida['outros']['nao_atribuidos']['janela'], [1, 2])
        self.assertEqual(saida['outros']['processos'], 1)
        for valor in ([], dict(janela=[0, 0], total=[-1, 0])):
            self.d['expirados'] = valor
            with self.assertRaises(ValueError):
                self.montar()

    def test_e29_c6e_nome_sem_controles_ou_bidi(self):
        self.processos[3]['nome'] = 'wo\u202erk\x1ber.exe'
        self.assertEqual(self.montar()['sessoes'][0]['conexoes'][0]['processo'], 'worker.exe')

    def test_e29_c7_mesmo_agente_externo_e_agente_diferente_proprio(self):
        for agente, pid_raiz in (('claude', 1), ('codex', 2)):
            with self.subTest(agente=agente):
                self.processos[2] = processo(2, 1, 1, agente)
                sessao = self.montar()['sessoes'][0]
                self.assertEqual((sessao['agente'], sessao['pid_raiz']), (agente, pid_raiz))
