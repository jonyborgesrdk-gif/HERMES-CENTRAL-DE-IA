from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
from lib.fontes import maestri_wire


def setUpModule():
    p = patch.object(urllib.request, 'build_opener', side_effect=AssertionError('rede real proibida'))
    p.start()
    unittest.addModuleCleanup(p.stop)


class WireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.ctx = {'dir': self.tmp.name, 'http_get': self.http}

    def ativar(self):
        (Path(self.tmp.name) / 'wire.conf').write_text('WIRE_TOKEN=token-sintetico\n', encoding='utf-8')

    def http(self, url, timeout, headers):
        self.calls.append((url, timeout, headers))
        if url.endswith('workspaces'):
            return {'workspaces': [{'id': 'abc', 'name': 'Exemplo'}]}
        return {'items': [{'type': 'terminal', 'terminal': {'id': 't', 'name': 'Agente',
                'agentType': 'codex', 'status': 'running', 'isRunning': True, 'preview': []}}]}

    def test_desligado(self):
        self.assertEqual(maestri_wire.ler(self.ctx), {'ligado': False})
        self.assertEqual(self.calls, [])

    def test_workspaces_feed(self):
        self.ativar()
        r = maestri_wire.ler(self.ctx)
        self.assertEqual(r['terminais'][0]['workspace'], 'Exemplo')
        self.assertTrue(r['terminais'][0]['rodando'])
        self.assertEqual(self.calls[0][1], 3)
        self.assertEqual(self.calls[0][2]['Authorization'], 'Bearer token-sintetico')

    def test_sem_preview_nao_parado(self):
        r = maestri_wire.interpretar({'status': 'running', 'preview': []}, 'w')
        self.assertEqual(r['tela'], 'sem tela')
        self.assertEqual(r['status'], 'running')

    def test_terminal_direto_limite_e_mascara(self):
        r = maestri_wire.interpretar({'preview': ['sk-' + 'a' * 40] * 12}, 'w')
        self.assertEqual(len(r['preview']), 10)
        self.assertNotIn('sk-' + 'a' * 40, str(r['preview']))

    def test_listas_e_item_direto(self):
        self.ativar()
        self.ctx['http_get'] = lambda url, **kw: ([{'id': 'w'}] if url.endswith('workspaces')
            else [{'type': 'terminal', 'agentType': 'custom', 'name': 'Agente'}])
        self.assertEqual(maestri_wire.ler(self.ctx)['terminais'][0]['tipo'], 'custom')

    def test_erro_nao_vaza_token(self):
        self.ativar()
        def falha(*a, **kw):
            raise RuntimeError('token-sintetico')
        self.ctx['http_get'] = falha
        r = maestri_wire.ler(self.ctx)
        self.assertTrue(r['ligado'])
        self.assertNotIn('token-sintetico', str(r))
