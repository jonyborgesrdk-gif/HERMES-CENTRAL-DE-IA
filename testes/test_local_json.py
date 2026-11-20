"""E26: opt-in, isolamento do feed público, mescla e substituição atômica local."""
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import http.client
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib.semjanela import sem_janela_kwargs

RAIZ = Path(__file__).resolve().parents[1]
FLAGS = ('PAINEL_GASTOS', 'PAINEL_PRESENCA', 'PAINEL_PRESENCA_LINUX', 'PAINEL_COTA_CODEX_LOCAL',
         'PAINEL_COTA_GROK_BOT', 'PAINEL_COTA_CURSOR', 'PAINEL_VIGILANTE', 'PAINEL_ERROS',
         'PAINEL_AGENTES_EXTRA', 'PAINEL_REDE', 'PAINEL_MAQUINA', 'PAINEL_CUSTO',
         'PAINEL_CONEXOES', 'PAINEL_COMANDO', 'PAINEL_REDE_SESSAO')
ITEM = {'proto': 'tcp', 'familia': 4, 'local_ip': '192.0.2.123', 'local_porta': 49152,
        'remoto_ip': '203.0.113.234', 'remoto_porta': 443, 'estado': 'ESTABLISHED',
        'pid': 456789, 'processo': 'LOCAL_EXE_FIXTURE.exe'}
SECAO = {'estado': 'ok', 'itens': [ITEM], 'portas_abertas': [], 'omitidas': 0,
         'processos': [{'pid': 456789, 'processo': 'LOCAL_EXE_FIXTURE.exe', 'total': 1}]}


class LocalJson(unittest.TestCase):
    def setUp(self):
        loader = SourceFileLoader('coletor_e26', str(RAIZ / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name).resolve()
        self.dir = self.raiz / 'dados'
        self.saida = self.raiz / 'publico'
        self.arquivo = self.dir / 'local.json'
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True, telas=None,
                                    intervalo=2, intervalo_prs=180, intervalo_sonda=300, saida=self.saida)
        ambiente = {nome: '' for nome in FLAGS}
        ambiente['PAINEL_IAS_DIR'] = str(self.dir)
        self.env = patch.dict(os.environ, ambiente)
        self.env.start()
        self.addCleanup(self.env.stop)
        quadro = patch.object(self.coletor, 'ler_quadro', return_value=(self.coletor.comum.quadro_vazio(), None))
        quadro.start()
        self.addCleanup(quadro.stop)
        hora = patch.object(self.coletor.comum, 'agora', return_value=datetime(2026, 11, 19, tzinfo=timezone.utc))
        hora.start()
        self.addCleanup(hora.stop)
        self.fonte = Mock(return_value={'conexoes': SECAO})
        self.cache = {}

    def passada(self, hora=1000):
        return self.coletor.passada_viva(self.args, fontes={'conexoes': self.fonte},
                                         ctx={'dir': self.dir, 'agora': hora}, cache=self.cache)

    def ler(self):
        return json.loads(self.arquivo.read_text(encoding='utf-8'))

    def anterior(self, valor):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.arquivo.write_text(json.dumps(valor), encoding='utf-8')

    def conferir_publico(self, dados):
        serializados = [json.dumps(dados), (self.saida / 'dados.json').read_text(encoding='utf-8'),
                        (self.saida / 'dados.js').read_text(encoding='utf-8')]
        for texto in serializados:
            for proibido in ('192.0.2.123', '203.0.113.234', '49152', '443', '456789',
                             'LOCAL_EXE_FIXTURE.exe', 'local_ip', 'local_porta', 'remoto_ip', '"pid"'):
                self.assertNotIn(proibido, texto)
        self.assertNotIn('conexoes', dados)
        self.assertFalse((self.saida / 'local.json').exists())

    def test_valores_desligados_nao_criam_nem_alteram_arquivo_e_feed_identico(self):
        referencia = None
        for valor in ('', '0', 'true', 'sim', '01', ' 1', '1 '):
            with self.subTest(valor=valor), patch.dict(os.environ, PAINEL_CONEXOES=valor):
                dados = self.passada()
                self.assertFalse(self.arquivo.exists())
                if referencia is None:
                    referencia = dados
                self.assertEqual(dados, referencia)
                self.assertNotIn('conexoes', self.coletor.extras_ativos())
                self.assertNotIn('conexoes', self.coletor.fontes_vivas(True))
                self.assertNotIn('conexoes', dados['fontes'])
        self.anterior({'conexoes': SECAO, 'comando': {'fixture': True}})
        antes = self.arquivo.read_bytes()
        with patch.object(self.coletor.comum, 'gravar_atomico') as gravar:
            self.coletor.gravar_local({'conexoes': SECAO}, {'dir': self.dir}, self.saida)
            gravar.assert_not_called()
        self.passada()
        self.assertEqual(self.arquivo.read_bytes(), antes)
        self.fonte.assert_not_called()

    def test_1_registra_fonte_grava_local_e_publica_so_saude(self):
        with patch.dict(os.environ, PAINEL_CONEXOES='1'):
            self.assertEqual(self.coletor.extras_ativos(), ('conexoes',))
            self.assertEqual(self.coletor.fontes_vivas(True)['conexoes'].__module__, 'fontes.conexoes')
            dados = self.passada()
        self.assertEqual(self.ler()['conexoes'], SECAO)
        self.assertEqual(set(self.ler()), {'gerado_em', 'gerado_em_epoch', 'conexoes'})
        self.assertEqual(self.ler()['gerado_em_epoch'], 1795046400)
        self.assertEqual(dados['fontes']['conexoes'], {'ok': True, 'erro': None})
        self.conferir_publico(dados)

    def test_e27_opt_in_exato_feed_identico_sem_leitura(self):
        comando = Mock(return_value={'comando': {'estado': 'ok', 'sessoes': []}})
        referencia = self.passada()
        bytes_antes = (self.saida / 'dados.json').read_bytes()
        for valor in ('', '0', 'true', '01', ' 1', '1 '):
            with self.subTest(valor=valor), patch.dict(os.environ, PAINEL_COMANDO=valor):
                dados = self.coletor.passada_viva(self.args, fontes={'comando_atual': comando},
                    ctx={'dir': self.dir, 'agora': 1000}, cache=self.cache)
                self.assertEqual(dados, referencia)
                self.assertEqual((self.saida / 'dados.json').read_bytes(), bytes_antes)
                self.assertNotIn('comando_atual', self.coletor.extras_ativos())
                self.assertNotIn('comando_atual', self.coletor.fontes_vivas(True))
                self.assertFalse(self.arquivo.exists())
        comando.assert_not_called()
        with patch.dict(os.environ, PAINEL_COMANDO='1'):
            self.assertIn('comando_atual', self.coletor.extras_ativos())
            self.assertEqual(self.coletor.fontes_vivas(True)['comando_atual'].__module__, 'fontes.comando_atual')

    def test_e27_cadencia_mescla_unica_e_falha_constante(self):
        comando = Mock(return_value={'comando': {'estado': 'ok', 'sessoes': []}})
        with patch.dict(os.environ, PAINEL_CONEXOES='1', PAINEL_COMANDO='1'), \
                patch.object(self.coletor, 'gravar_local', wraps=self.coletor.gravar_local) as gravar:
            for hora, chamadas in ((1000, 1), (1001, 1), (1002, 2), (1003, 2), (1004, 3)):
                dados = self.coletor.passada_viva(self.args,
                    fontes={'conexoes': self.fonte, 'comando_atual': comando},
                    ctx={'dir': self.dir, 'agora': hora}, cache=self.cache)
                self.assertEqual(comando.call_count, chamadas)
                self.assertEqual(gravar.call_count, chamadas)
                self.assertEqual(dados['fontes']['comando_atual'], {'ok': True, 'erro': None})
            self.assertEqual(set(gravar.call_args_list[0].args[0]), {'conexoes', 'comando'})
            self.assertEqual(self.ler()['conexoes'], SECAO)
            for retorno in ({'erro': 'SEGREDO_E27'}, {}, None, OSError('SEGREDO_E27')):
                self.cache.clear()
                if isinstance(retorno, Exception):
                    comando.side_effect = retorno
                else:
                    comando.side_effect, comando.return_value = None, retorno
                dados = self.coletor.passada_viva(self.args, fontes={'comando_atual': comando},
                    ctx={'dir': self.dir, 'agora': 1006}, cache=self.cache)
                self.assertEqual(dados['fontes']['comando_atual'], {'ok': False, 'erro': self.coletor.ERRO_COMANDO})
                self.assertEqual(self.ler()['comando'], {'estado': 'erro', 'erro': self.coletor.ERRO_COMANDO})
                self.assertNotIn('SEGREDO_E27', json.dumps(dados))

    def test_e27_quatro_agentes_token_nao_vaza_em_local_log_ou_publico(self):
        from testes.test_fontes_comando_atual import FontesComando, TOKEN, TOKENS_FORMATOS, claude, codex, grok
        from lib.fontes import comando_atual
        fixture = FontesComando()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        texto_fixture = 'echo ' + ' '.join((TOKEN,) + TOKENS_FORMATOS)
        fixture.arquivo('claude', [claude(argumentos={'command': texto_fixture})])
        fixture.arquivo('codex', [codex(argumentos={'cmd': texto_fixture})])
        fixture.arquivo('grok', [grok(argumentos={'command': texto_fixture})])
        fixture.banco([{'id': 'h', 'function': {'name': 'terminal',
                       'arguments': json.dumps({'command': texto_fixture})}}])
        ctx = dict(fixture.ctx, dir=self.dir)
        montar = self.coletor.vivo.montar_agentes
        def verificar(leituras, *args):
            self.assertNotIn('comando_atual', leituras)
            self.assertNotIn(TOKEN, json.dumps(leituras))
            return montar(leituras, *args)
        with patch.dict(os.environ, PAINEL_COMANDO='1'), patch('logging.Logger.handle') as logs, \
                patch.object(self.coletor.vivo, 'montar_agentes', side_effect=verificar):
            dados = self.coletor.passada_viva(self.args, fontes={'comando_atual': comando_atual.ler}, ctx=ctx)
        itens = self.ler()['comando']['sessoes']
        self.assertEqual({i['agente'] for i in itens}, {'claude', 'codex', 'grok', 'hermes'})
        self.assertNotIn(TOKEN, self.arquivo.read_text(encoding='utf-8'))
        artefatos = [self.arquivo.read_text(encoding='utf-8'), json.dumps(itens),
                     repr(logs.call_args_list), json.dumps(dados)]
        for caminho in (self.saida / 'dados.json', self.saida / 'dados.js'):
            artefatos.append(caminho.read_text(encoding='utf-8'))
        for bruto in artefatos:
            for token in TOKENS_FORMATOS + ('E27CORPOFALSO',):
                self.assertNotIn(token, bruto)
                self.assertNotIn(json.dumps(token)[1:-1], bruto)
        for caminho in (self.saida / 'dados.json', self.saida / 'dados.js'):
            bruto = caminho.read_text(encoding='utf-8')
            for proibido in (TOKEN, 'echo ', 'desde_epoch', 'tipo_tarefa', 'hermes-1', 'claude-1'):
                self.assertNotIn(proibido, bruto)
        self.assertNotIn(TOKEN, repr(logs.call_args_list))
        self.assertNotIn('comando', dados)
        self.assertNotIn('comando_atual', {k: v for k, v in dados.items() if k != 'fontes'})
        self.assertEqual(dados['fontes']['comando_atual'], {'ok': True, 'erro': None})

    def test_e27_falha_de_gravacao_e_teto_conjunto_unicode(self):
        secao = {'estado': 'ok', 'sessoes': [dict(agente='claude', sessao='12345678', projeto='漢' * 128,
            ferramenta='f' * 64, texto='漢' * 1999 + '…', em_curso=True, desde_epoch=1000,
            tipo_tarefa='outro', tipo_tarefa_inferido=True) for _ in range(30)]}
        comando = Mock(return_value={'comando': secao})
        with patch.dict(os.environ, PAINEL_COMANDO='1'), \
                patch.object(self.coletor, 'gravar_local', side_effect=OSError('SEGREDO_E27')):
            dados = self.coletor.passada_viva(self.args, fontes={'comando_atual': comando},
                                            ctx={'dir': self.dir, 'agora': 1000})
        self.assertEqual(dados['fontes']['comando_atual'], {'ok': False, 'erro': self.coletor.ERRO_COMANDO})
        from lib.fontes.conexoes import agregar
        conexoes = agregar([dict(ITEM, processo='漢' * 200, pid=i) for i in range(5000)], 0)
        with patch.dict(os.environ, PAINEL_COMANDO='1', PAINEL_CONEXOES='1'):
            self.coletor.gravar_local({'comando': secao, 'conexoes': conexoes}, {'dir': self.dir}, self.saida)
        self.assertLessEqual(self.arquivo.stat().st_size, self.coletor.MAX_LOCAL_GRAVADO)
        self.assertEqual(self.ler()['comando'], secao)
        self.assertGreater(self.ler()['conexoes']['omitidas'], 0)

    def test_cadencia_5_segundos(self):
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), \
                patch.object(self.coletor, 'gravar_local', wraps=self.coletor.gravar_local) as gravar:
            for hora, chamadas in ((1000, 1), (1004, 1), (1005, 2), (1009, 2), (1010, 3)):
                self.passada(hora)
                self.assertEqual(self.fonte.call_count, chamadas)
                self.assertEqual(gravar.call_count, chamadas)

    def test_falha_na_fonte_constante_sem_reter_conexoes(self):
        with patch.dict(os.environ, PAINEL_CONEXOES='1'):
            self.passada()
            for retorno in ({'erro': '192.0.2.123:49152 PID 456789'}, {}, None):
                self.fonte.return_value = retorno
                self.cache.clear()
                dados = self.passada(1005)
                self.assertEqual(dados['fontes']['conexoes'], {'ok': False, 'erro': self.coletor.ERRO_CONEXOES})
                self.assertEqual(self.ler()['conexoes'], {'estado': 'erro', 'erro': self.coletor.ERRO_CONEXOES})
                self.conferir_publico(dados)

    def test_excecao_sensivel_nao_vaza(self):
        self.fonte.side_effect = OSError('192.0.2.123:49152 PID 456789')
        with patch.dict(os.environ, PAINEL_CONEXOES='1'):
            dados = self.passada()
        self.conferir_publico(dados)
        self.assertEqual(dados['fontes']['conexoes']['erro'], self.coletor.ERRO_CONEXOES)

    def test_conteudo_retirado_antes_dos_montadores_publicos(self):
        montar = self.coletor.vivo.montar_agentes
        def verificar(leituras, *args):
            self.assertNotIn('conexoes', leituras)
            return montar(leituras, *args)
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), \
                patch.object(self.coletor.vivo, 'montar_agentes', side_effect=verificar):
            self.conferir_publico(self.passada())

    def test_mescla_secoes_ativas_remove_desligadas(self):
        self.anterior({'gerado_em': 'antigo', 'comando': {'fixture': True},
                       'rede_sessao': {'fixture': 'desligada'}, 'conexoes': {}})
        with patch.dict(os.environ, PAINEL_CONEXOES='1', PAINEL_COMANDO='1'):
            self.coletor.gravar_local({'conexoes': SECAO}, {'dir': self.dir}, self.saida)
            self.assertEqual(self.ler()['comando'], {'fixture': True})
            self.assertEqual(self.ler()['conexoes'], SECAO)
            self.assertNotIn('rede_sessao', self.ler())
        with patch.dict(os.environ, PAINEL_COMANDO='1'):
            self.coletor.gravar_local({'comando': {'fixture': 'nova'}}, {'dir': self.dir}, self.saida)
        self.assertNotIn('conexoes', self.ler())
        self.assertEqual(self.ler()['comando'], {'fixture': 'nova'})

    def test_mesmo_diretorio_padrao_da_e26a_e_ctx_tem_precedencia(self):
        with patch.dict(os.environ, PAINEL_CONEXOES='1'):
            self.coletor.gravar_local({'conexoes': SECAO}, {}, self.saida)
            self.assertTrue(self.arquivo.exists())
            outro = self.raiz / 'outro'
            self.coletor.gravar_local({'conexoes': SECAO}, {'dir': outro}, self.saida)
            self.assertEqual(json.loads((outro / 'local.json').read_text(encoding='utf-8'))['conexoes'], SECAO)

    def test_destino_publico_recusado_sem_criar_arquivo(self):
        publicas = (self.raiz / 'publico_real', self.saida)
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), \
                patch.object(self.coletor.comum, 'PASTA_PUBLICA', publicas[0]):
            for publica in publicas:
                for pasta in (publica, publica / 'subpasta', publica / '..' / publica.name):
                    with self.subTest(pasta=pasta), self.assertRaises(ValueError):
                        self.coletor.gravar_local({'conexoes': SECAO}, {'dir': pasta}, self.saida)
                if os.name == 'nt':
                    with self.assertRaises(ValueError):
                        self.coletor.gravar_local({'conexoes': SECAO},
                                                  {'dir': '\\\\?\\' + str(publica)}, self.saida)
                self.assertFalse(publica.exists())

    def test_falha_gravar_local_nao_vaza_nem_impede_feed_publico(self):
        self.anterior({'conexoes': {'fixture': 'anterior'}})
        antes = self.arquivo.read_bytes()
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), \
                patch.object(self.coletor, 'gravar_local', side_effect=OSError('192.0.2.123:49152 PID 456789')):
            dados = self.passada()
        self.assertEqual(self.arquivo.read_bytes(), antes)
        self.assertEqual(dados['fontes']['conexoes'], {'ok': False, 'erro': self.coletor.ERRO_CONEXOES})
        self.conferir_publico(dados)

    def test_gravacao_atomica_substitui_arquivo_completo_mesma_pasta(self):
        self.anterior({'conexoes': {'fixture': 'anterior'}})
        antes = self.arquivo.read_bytes()
        replace = self.coletor.comum.os.replace
        def verificar(origem, destino):
            self.assertEqual(Path(origem).parent, self.dir)
            self.assertEqual(Path(destino), self.arquivo)
            self.assertEqual(self.arquivo.read_bytes(), antes)
            self.assertEqual(json.loads(Path(origem).read_text(encoding='utf-8'))['conexoes'], SECAO)
            replace(origem, destino)
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), \
                patch.object(self.coletor.comum.os, 'replace', side_effect=verificar) as troca:
            self.coletor.gravar_local({'conexoes': SECAO}, {'dir': self.dir}, self.saida)
            troca.assert_called_once()
        self.assertEqual(self.ler()['conexoes'], SECAO)
        self.assertEqual(list(self.dir.glob('*.tmp')), [])

    def test_falha_replace_preserva_anterior_e_remove_temporario(self):
        self.anterior({'conexoes': {'fixture': 'anterior'}})
        antes = self.arquivo.read_bytes()
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), \
                patch.object(self.coletor.comum.os, 'replace', side_effect=OSError('falha')), self.assertRaises(OSError):
            self.coletor.gravar_local({'conexoes': SECAO}, {'dir': self.dir}, self.saida)
        self.assertEqual(self.arquivo.read_bytes(), antes)
        self.assertEqual(list(self.dir.glob('*.tmp')), [])

    def test_arquivo_anterior_corrompido_ou_nao_objeto(self):
        self.dir.mkdir()
        with patch.dict(os.environ, PAINEL_CONEXOES='1'):
            for anterior in ('{', '[]', 'null'):
                self.arquivo.write_text(anterior, encoding='utf-8')
                self.coletor.gravar_local({'conexoes': SECAO}, {'dir': self.dir}, self.saida)
                self.assertEqual(self.ler()['conexoes'], SECAO)

    def test_anterior_utf8_invalido_ou_recursivo_nao_congela_passada(self):
        self.dir.mkdir()
        with patch.dict(os.environ, PAINEL_CONEXOES='1'):
            for bruto in (b'\xff\xfe', b'[' * 2000 + b']' * 2000):
                self.arquivo.write_bytes(bruto)
                self.cache.clear()
                dados = self.passada()
                self.assertEqual(self.ler()['conexoes'], SECAO)
                self.assertEqual(dados['fontes']['conexoes'], {'ok': True, 'erro': None})
                self.conferir_publico(dados)

    def test_qualquer_excecao_no_json_anterior_descarta_o_anterior(self):
        with patch.dict(os.environ, PAINEL_CONEXOES='1', PAINEL_COMANDO='1'):
            for erro in (MemoryError(), RuntimeError(), RecursionError(),
                         UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'fixture')):
                self.anterior({'comando': {'fixture': True}})
                with patch.object(self.coletor.json, 'loads', side_effect=erro):
                    self.coletor.gravar_local({'conexoes': SECAO}, {'dir': self.dir}, self.saida)
                self.assertEqual(self.ler()['conexoes'], SECAO)
                self.assertNotIn('comando', self.ler())

    def test_anterior_maior_que_teto_nao_e_interpretado(self):
        self.dir.mkdir()
        self.arquivo.write_bytes(b' ' * (self.coletor.MAX_LOCAL_LIDO + 1))
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), patch.object(self.coletor.json, 'loads') as ler:
            self.coletor.gravar_local({'conexoes': SECAO}, {'dir': self.dir}, self.saida)
            ler.assert_not_called()
        self.assertEqual(self.ler()['conexoes'], SECAO)

    def test_replace_ocupado_transitorio_reutiliza_temporario_ate_sucesso(self):
        self.anterior({'conexoes': {'fixture': 'anterior'}})
        antes = self.arquivo.read_bytes()
        replace = self.coletor.comum.os.replace
        origens = []
        def trocar(origem, destino):
            origens.append(origem)
            self.assertEqual(self.arquivo.read_bytes(), antes)
            if len(origens) < 3:
                raise PermissionError('fixture sensível')
            replace(origem, destino)
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), patch('time.sleep') as esperar, \
                patch.object(self.coletor.comum.os, 'replace', side_effect=trocar):
            self.assertTrue(self.coletor.gravar_local({'conexoes': SECAO}, {'dir': self.dir}, self.saida))
        self.assertEqual(len(origens), 3)
        self.assertEqual(len(set(origens)), 1)
        self.assertEqual(esperar.call_args_list, [unittest.mock.call(0.05)] * 2)
        self.assertEqual(self.ler()['conexoes'], SECAO)
        self.assertEqual(list(self.dir.glob('*.tmp')), [])

    def test_replace_ocupado_esgota_limite_log_constante_e_fonte_continua_ok(self):
        self.anterior({'conexoes': {'fixture': 'anterior'}})
        antes = self.arquivo.read_bytes()
        replace = self.coletor.comum.os.replace
        tentativas, ocupado = [], [True]
        def trocar(origem, destino):
            if Path(destino) == self.arquivo:
                tentativas.append(origem)
                if ocupado[0]:
                    raise PermissionError('192.0.2.123:49152 PID 456789')
            replace(origem, destino)
        with patch.dict(os.environ, PAINEL_CONEXOES='1'), patch('time.sleep') as esperar, \
                patch.object(self.coletor.comum.os, 'replace', side_effect=trocar), \
                self.assertLogs('painel.coletor', level='WARNING') as logs:
            dados = self.passada(1000)
            self.assertEqual(len(tentativas), 6)
            self.assertEqual(esperar.call_args_list, [unittest.mock.call(0.05)] * 5)
            self.assertEqual(self.arquivo.read_bytes(), antes)
            self.assertEqual(dados['fontes']['conexoes'], {'ok': True, 'erro': None})
            self.conferir_publico(dados)
            self.passada(1002)
            self.assertEqual(len(tentativas), 6)
            ocupado[0] = False
            self.passada(1005)
            self.assertEqual(len(tentativas), 7)
        self.assertEqual(logs.output, ['WARNING:painel.coletor:local.json: gravação adiada por arquivo ocupado'])
        self.assertEqual(self.ler()['conexoes'], SECAO)
        self.assertEqual(list(self.dir.glob('*.tmp')), [])

    def test_helper_publico_preserva_uma_unica_tentativa(self):
        with patch.object(self.coletor.comum.os, 'replace', side_effect=PermissionError()), \
                patch('time.sleep') as esperar, self.assertRaises(PermissionError):
            self.coletor.comum.gravar_atomico(self.raiz / 'fixture.txt', 'fixture')
        esperar.assert_not_called()
        self.assertEqual(list(self.raiz.glob('*.tmp')), [])

    def test_corta_5000_itens_unicode_ate_3_5_mib_e_7422_responde_200(self):
        from lib.fontes.conexoes import agregar
        import servidor
        itens = [dict(ITEM, proto='udp', estado=None, remoto_ip=None, remoto_porta=None,
                      pid=456789 + i, local_porta=1024 + i, processo='漢' * 200 + '.exe') for i in range(5000)]
        secao = agregar(itens, 7)
        self.assertGreater(len(json.dumps(secao)), self.coletor.MAX_LOCAL_GRAVADO)
        self.anterior({'comando': {'fixture': 'preservada'}})
        with patch.dict(os.environ, PAINEL_CONEXOES='1', PAINEL_COMANDO='1'):
            self.coletor.gravar_local({'conexoes': secao}, {'dir': self.dir}, self.saida)
        bruto = self.arquivo.read_bytes()
        self.assertLessEqual(len(bruto), self.coletor.MAX_LOCAL_GRAVADO)
        self.assertTrue(bruto.isascii())
        local = self.ler()
        c = local['conexoes']
        n = len(c['itens'])
        self.assertGreater(n, 0)
        self.assertLess(n, 5000)
        self.assertEqual(c['omitidas'], 7 + 5000 - n)
        self.assertEqual(len(c['portas_abertas']), n)
        self.assertEqual(len(c['processos']), n)
        self.assertEqual(sum(p['total'] for p in c['processos']), n)
        self.assertEqual(sum(p['udp'] for p in c['processos']), n)
        self.assertEqual(c['itens'][-1]['pid'], 456789 + n - 1)
        self.assertEqual(local['comando'], {'fixture': 'preservada'})
        self.assertEqual(len(secao['itens']), 5000)
        self.assertEqual(secao['omitidas'], 7)
        servidor_local = servidor.PainelLocalHTTP(0, threading.Event())
        servidor_local.fio.start()
        conn = http.client.HTTPConnection('127.0.0.1', servidor_local.server_port, timeout=5)
        try:
            conn.request('GET', '/local.json')
            resposta = conn.getresponse()
            self.assertEqual(resposta.status, 200)
            self.assertEqual(json.loads(resposta.read())['conexoes']['omitidas'], c['omitidas'])
        finally:
            conn.close()
            servidor_local.parar.set()
            servidor_local.fio.join(3)
        self.assertFalse(servidor_local.fio.is_alive())
        self.assertEqual(servidor_local.socket.fileno(), -1)


class PaginaLocal(unittest.TestCase):
    def test_dom_filtros_ordenacao_atualizacao_e_falha(self):
        html = (RAIZ / 'publico_local/index.html').read_text(encoding='utf-8')
        scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(len(re.findall(r'<style>', html)), 1)
        self.assertNotIn('innerHTML', html)
        # DOM mínimo: testa somente a fronteira usada pela página, inclusive textContent.
        harness = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Elemento {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.value = ''; this.events = {}; }
  appendChild(child) { if(child.tag === 'fragment') this.children.push(...child.children); else this.children.push(child); }
  replaceChildren(...children) { this.children = []; children.forEach(c => this.appendChild(c)); }
  addEventListener(event, callback) { this.events[event] = callback; }
}
const elementos = {};
const document = {getElementById: id => elementos[id] ||= new Elemento(id),
  createElement: tag => new Elemento(tag), createDocumentFragment: () => new Elemento('fragment')};
const linha = (processo, pid, proto = 'tcp') => ({processo, pid, proto, familia: 6,
  local_ip: 'fe80::1%7', local_porta: 49152, remoto_ip: '::1', remoto_porta: 443, estado: 'ESTABLISHED'});
let dados = {gerado_em: 'agora', gerado_em_epoch: 2000,
  comando: {estado: 'ok', sessoes: [{agente: 'codex', sessao: '12345678', projeto: 'painel', ferramenta: 'exec_command',
    texto: '<img src=x onerror=boom>', desde_epoch: 1990, em_curso: true, tipo_tarefa: 'testes', tipo_tarefa_inferido: true}]},
  conexoes: {estado: 'ok', omitidas: 2, processos: [{}, {}],
  itens: [linha('z.exe', 10), linha('<img src=x onerror=boom>', 11)],
  portas_abertas: [{proto: 'tcp', local_ip: '::', porta: 443, processos: [{processo: 'z.exe', pid: 10}]},
    {proto: 'udp', local_ip: '::1', porta: 49152, processos: [{processo: 'a.exe', pid: 12}]}]}};
let falha = false;
let agoraMs = 2000000;
let proximoTimer = 0;
const timers = new Map();
const intervalos = {};
function avancar(ms) {
  agoraMs += ms;
  for (const [id, timer] of timers) if (timer.ate <= agoraMs) {timers.delete(id); timer.fn();}
}
const contexto = {document, fetch: async (url, opts) => {
  assert.equal(url, '/local.json'); assert.equal(opts.cache, 'no-store');
  return {ok: !falha, json: async () => dados};
}, setInterval: (fn, tempo) => {assert.ok([1000, 2000, 5000].includes(tempo)); intervalos[tempo] = fn;},
  Date: {now: () => agoraMs},
  setTimeout: (fn, ms) => {assert.equal(ms, 200); const id = ++proximoTimer; timers.set(id, {fn, ate: agoraMs + ms}); return id;},
  clearTimeout: id => timers.delete(id)};
vm.createContext(contexto);
vm.runInContext(SCRIPT, contexto);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  const comando = elementos.comandos.children[0];
  assert.equal(comando.children[4].children[0].tag, 'code');
  assert.equal(comando.children[4].children[0].textContent, '<img src=x onerror=boom>');
  assert.equal(comando.children[4].children[0].children.length, 0);
  assert.equal(comando.children[5].children[0].textContent, '10 s');
  assert.equal(comando.children[6].children[0].textContent, 'em curso');
  assert.equal(comando.children[7].children[1].textContent, 'inferência');
  assert.match(elementos['situacao-comando'].textContent, /Cursor-agent: sem leitura/);
  const codigoSelecionado = comando.children[4].children[0];
  const portasAntes = elementos.portas.children[0];
  await vm.runInContext('atualizar()', contexto);
  assert.equal(elementos.comandos.children[0], comando);
  assert.equal(elementos.portas.children[0], portasAntes);
  dados.comando.sem_leitura = ['cursor-agent', 'hermes'];
  await vm.runInContext('atualizar()', contexto);
  assert.equal(elementos.comandos.children[0], comando);
  assert.match(elementos['situacao-comando'].textContent, /Há fontes sem leitura/);
  agoraMs += 1000; intervalos[1000]();
  assert.equal(elementos.comandos.children[0].children[4].children[0], codigoSelecionado);
  assert.equal(comando.children[5].children[0].textContent, '11 s');
  agoraMs -= 1000; intervalos[1000]();
  dados.comando.sessoes[0].em_curso = false;
  dados.comando.sessoes[0].sem_resultado = true;
  await intervalos[2000]();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elementos.comandos.children[0].children[6].children[0].textContent, 'última');
  assert.equal(elementos.comandos.children[0].children[6].children[1].textContent, 'sem resultado');
  assert.equal(elementos.portas.children[0].children[2].textContent, 'a.exe');
  assert.equal(elementos.estabelecidas.children[0].children[0].textContent, '<img src=x onerror=boom>');
  assert.equal(elementos.estabelecidas.children[0].children[0].children.length, 0);
  assert.equal(elementos.estabelecidas.children[0].children[2].textContent, '[fe80::1%7]:49152');
  assert.equal(elementos.idade.textContent, 'Dados de há 0 s.');
  elementos.filtro.value = 'z.exe'; elementos.filtro.events.input();
  assert.equal(elementos.portas.children[0].children[2].textContent, 'a.exe');
  avancar(150);
  elementos.filtro.events.input();
  assert.equal(timers.size, 1);
  avancar(199);
  assert.equal(elementos.portas.children[0].children[2].textContent, 'a.exe');
  avancar(1);
  assert.equal(elementos.portas.children.length, 1);
  assert.equal(elementos.portas.children[0].children[2].textContent, 'z.exe');
  elementos.proto.value = 'udp'; elementos.proto.events.change();
  assert.equal(elementos.portas.children[0].children[0].textContent, 'Nenhum resultado.');
  elementos.filtro.value = ''; elementos.filtro.events.input(); avancar(200);
  assert.equal(elementos.portas.children[0].children[2].textContent, 'a.exe');
  assert.equal(elementos.estabelecidas.children[0].children[0].textContent, 'Nenhum resultado.');
  await vm.runInContext('atualizar()', contexto);
  assert.equal(elementos.proto.value, 'udp'); assert.equal(elementos.filtro.value, '');
  elementos.filtro.value = 'a.exe'; elementos.filtro.events.input();
  await vm.runInContext('atualizar()', contexto);
  assert.equal(elementos.filtro.value, 'a.exe');
  agoraMs = 2030000; intervalos[1000]();
  assert.equal(elementos.idade.textContent, 'Dados de há 30 s.');
  assert.equal(elementos.idade.className, '');
  agoraMs = 2031000; intervalos[1000]();
  assert.match(elementos.idade.textContent, /Dados de há 31 s.*desatualizados/);
  assert.equal(elementos.idade.className, 'aviso');
  dados.gerado_em_epoch = 2031;
  await vm.runInContext('atualizar()', contexto);
  assert.equal(elementos.idade.textContent, 'Dados de há 0 s.');
  assert.equal(elementos.idade.className, '');
  falha = true; await vm.runInContext('atualizar()', contexto);
  assert.equal(elementos.comandos.children.length, 0);
  assert.equal(elementos.portas.children[0].children[0].textContent, 'Nenhum resultado.');
  assert.equal(elementos.gerado.textContent, 'sem leitura');
  assert.equal(elementos.idade.textContent, 'Idade dos dados: sem leitura.');
  falha = false; dados = {gerado_em: 'depois'};
  await vm.runInContext('atualizar()', contexto);
  assert.match(elementos.situacao.textContent, /desativada/);
  assert.equal(elementos.idade.textContent, 'Idade dos dados: sem leitura.');
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        codigo = 'const SCRIPT = ' + json.dumps(scripts[0]) + ';\n' + harness
        resultado = subprocess.run(['node', '-e', codigo], capture_output=True, text=True,
                                    timeout=15, **sem_janela_kwargs())
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


if __name__ == '__main__':
    unittest.main()
