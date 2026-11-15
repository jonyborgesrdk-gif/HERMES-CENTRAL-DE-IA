import base64
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
from lib import canais


def setUpModule():
    for alvo, nome in ((subprocess, 'run'), (subprocess, 'Popen'), (urllib.request, 'urlopen'),
                       (urllib.request, 'build_opener'), (canais, 'build_opener')):
        guarda = patch.object(alvo, nome, side_effect=AssertionError('canal real proibido'))
        guarda.start()
        unittest.addModuleCleanup(guarda.stop)


class CanaisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        self.logger = logging.getLogger('teste.canais')
        self.agora = 100000
        self.status = 500
        self.calls = []
        self.alerta = {'chave': 'parado:Agente', 'tipo': 'aberto', 'hora': self.agora,
                       'texto': 'Agente parou', 'canais': ['webhook']}

    def webhook(self):
        (self.pasta / 'webhook.conf').write_text('WEBHOOK_URL=https://exemplo.invalid/hook\nWEBHOOK_KEY=chave-sintetica', encoding='utf-8')
        def post(url, payload, **kw):
            self.calls.append((url, payload, kw))
            return self.status
        return canais.Webhook(self.pasta, http_post=post, agora=lambda: self.agora, logger=self.logger)

    def test_webhook_desligado_sem_config(self):
        c = canais.Webhook(self.pasta, http_post=lambda *a, **kw: self.fail('envio proibido'))
        self.assertFalse(c.enviar(self.alerta))
        self.assertFalse(c.fila.exists())

    def test_webhook_2xx_sem_fila(self):
        self.status = 204
        c = self.webhook()
        self.assertTrue(c.enviar(self.alerta))
        self.assertFalse(c.fila.exists())
        self.assertEqual(self.calls[0][2]['headers']['Authorization'], 'Bearer chave-sintetica')

    def test_webhook_3xx_e_5xx_ficam_na_fila(self):
        for codigo in (302, 500):
            with self.subTest(status=codigo):
                c = self.webhook()
                self.status = codigo
                self.assertFalse(c.enviar(self.alerta))
                itens = c._ler_fila()
                self.assertEqual(itens[-1]['tentativas'], 1)
                self.assertNotIn('chave-sintetica', c.fila.read_text())

    def test_reenvio_cinco_minutos_atrasado_e_remove(self):
        c = self.webhook()
        c.enviar(self.alerta)
        self.agora += 299
        self.assertEqual(c.reenviar(), 0)
        self.assertEqual(len(self.calls), 1)
        self.agora += 1
        self.status = 201
        self.assertEqual(c.reenviar(), 1)
        self.assertTrue(self.calls[-1][1]['atrasado'])
        self.assertEqual(c._ler_fila(), [])

    def test_reenvio_falha_atualiza_tentativas(self):
        c = self.webhook()
        c.enviar(self.alerta)
        self.agora += 300
        c.reenviar()
        item = c._ler_fila()[0]
        self.assertEqual(item['tentativas'], 2)
        self.assertTrue(item['alerta']['atrasado'])

    def test_desiste_em_vinte_quatro_horas(self):
        c = self.webhook()
        c.enviar(self.alerta)
        self.agora += 86400
        c.reenviar()
        self.assertEqual(c._ler_fila(), [])
        self.assertEqual(len(self.calls), 1)

    def test_desiste_na_quinquagesima_tentativa(self):
        c = self.webhook()
        c.enviar(self.alerta)
        itens = c._ler_fila()
        itens[0]['tentativas'] = 49
        c._salvar(itens)
        self.agora += 300
        c.reenviar()
        self.assertEqual(c._ler_fila(), [])

    def test_segredo_nao_entra_no_log_nem_fila(self):
        c = self.webhook()
        self.alerta['texto'] = 'texto chave-sintetica'
        with self.assertLogs(self.logger, level='WARNING') as logs:
            c.enviar(self.alerta)
        self.assertNotIn('chave-sintetica', str(logs.output))
        self.assertNotIn('chave-sintetica', c.fila.read_text())

    def test_http_real_nao_segue_redirect(self):
        self.assertIsNone(canais.SemRedirect().redirect_request(None, None, 302, '', {}, 'https://exemplo.invalid'))

    def test_toast_argumentos_inertes_e_timeout(self):
        calls = []
        texto = "ação ' ; $(Write-Output invasao) <xml>"
        def executar(argv, **kwargs):
            calls.append((argv, kwargs))
            return 0
        c = canais.Toast(executar=executar)
        self.assertTrue(c.enviar(dict(self.alerta, texto=texto)))
        argv, kw = calls[0]
        self.assertEqual(argv[4], canais.TOAST_SCRIPT)
        self.assertNotIn(texto, argv[4])
        self.assertEqual(base64.b64decode(argv[-1].strip("'")).decode(), texto)
        self.assertEqual(kw['timeout'], 10)
        self.assertIn('-NonInteractive', argv)

    def test_toast_falha_sem_excecao(self):
        def falhar(*a, **kw):
            raise TimeoutError
        self.assertFalse(canais.Toast(executar=falhar).enviar(self.alerta))

    def hindsight(self, env):
        def http(method, url, payload, **kw):
            self.calls.append((method, url, payload, kw))
            return 200
        return canais.Hindsight(self.pasta, http_fn=http, env=env, logger=self.logger)

    def test_hindsight_duplo_optin(self):
        self.hindsight({'HINDSIGHT_API_TOKEN': 'sintetico'})
        self.hindsight({'PAINEL_HINDSIGHT': '1'})
        self.assertEqual(self.calls, [])

    def test_hindsight_rotas_payload_sem_atividade(self):
        c = self.hindsight({'PAINEL_HINDSIGHT': '1', 'HINDSIGHT_API_TOKEN': 'sintetico'})
        for tipo in ('aberto', 'resolvido'):
            self.assertTrue(c.enviar(dict(self.alerta, tipo=tipo, atividade='privado', preview='privado', texto='privado')))
        self.assertEqual([x[0] for x in self.calls], ['PUT', 'POST', 'POST'])
        self.assertEqual(self.calls[0][1], c.BASE)
        self.assertEqual(self.calls[1][1], c.BASE + '/memories')
        payload = self.calls[1][2]
        self.assertTrue(payload['async'])
        self.assertEqual(payload['items'][0]['context'], 'painel-ias')
        self.assertEqual(payload['items'][0]['tags'], ['painel-ias', 'host:win'])
        self.assertNotIn('privado', str(payload))
        self.assertEqual(self.calls[1][3]['headers']['Authorization'], 'Bearer sintetico')

    def test_hindsight_config_local_e_falha(self):
        (self.pasta / 'hindsight.conf').write_text('HINDSIGHT_API_TOKEN=chave-local-sintetica', encoding='utf-8')
        c = self.hindsight({'PAINEL_HINDSIGHT': '1'})
        self.assertTrue(c.ligado)
        c.http = lambda *a, **kw: 500
        self.assertFalse(c.enviar(self.alerta))

    def test_hindsight_so_tipos_permitidos(self):
        c = self.hindsight({'PAINEL_HINDSIGHT': '1', 'HINDSIGHT_API_TOKEN': 'sintetico'})
        self.assertFalse(c.enviar(dict(self.alerta, tipo='preview')))
        self.assertFalse(c.enviar(dict(self.alerta, tipo='lembrete')))
        self.assertEqual(len(self.calls), 1)

    def test_resumo_hindsight_filtra_campos_e_tipos_aninhados(self):
        c = self.hindsight({'PAINEL_HINDSIGHT': '1', 'HINDSIGHT_API_TOKEN': 'sintetico'})
        alerta = dict(self.alerta, tipo='resumo_diario', resumo={
            'data': '2026-01-01', 'preview': 'privado',
            'alertas_por_tipo': {'parado': 2, 'atividade': 'privado'},
            'agentes_vistos': ['Agente', {'atividade': 'privado'}],
            'cotas_maximas': {'Agente': {'sessao_5h': 90, 'preview': 'privado'}}})
        self.assertTrue(c.enviar(alerta))
        conteudo = self.calls[-1][2]['items'][0]['content']
        self.assertNotIn('privado', conteudo)
        self.assertNotIn('atividade', conteudo)
        self.assertNotIn('preview', conteudo)
