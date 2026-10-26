import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
from lib.fontes import processos, servicos


def setUpModule():
    rede = patch.object(urllib.request, 'build_opener', side_effect=AssertionError('rede real proibida'))
    rede.start()
    unittest.addModuleCleanup(rede.stop)
    for nome in ('run', 'Popen'):
        p = patch.object(subprocess, nome, side_effect=AssertionError('subprocesso real proibido'))
        p.start()
        unittest.addModuleCleanup(p.stop)


class ServicosTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name)
        self.calls = []
        self.ctx = {'dir': p, 'agora': 100000, 'http_get': self.http,
                    'vigia_path': p / 'vigia', 'hermes_path': p / 'hermes'}

    def http(self, url, timeout, headers):
        self.calls.append((url, timeout))
        return {'status': 'healthy'} if url.endswith('health') else {'models': []}

    def test_health(self):
        r = servicos.ler(self.ctx)['servicos']
        self.assertEqual([s['estado'] for s in r[:2]], ['ok', 'ok'])
        self.assertTrue(all(t == 1 for _, t in self.calls))

    def test_timeout_fora(self):
        def falha(*a, **kw):
            raise TimeoutError
        self.ctx['http_get'] = falha
        self.assertEqual([s['estado'] for s in servicos.ler(self.ctx)['servicos'][:2]], ['fora', 'fora'])

    def test_batimento_ausente(self):
        self.assertEqual([s['estado'] for s in servicos.ler(self.ctx)['servicos'][2:]],
                         ['sem leitura', 'sem leitura'])

    def test_batimento_recente_e_velho(self):
        for chave in ('vigia_path', 'hermes_path'):
            p = self.ctx[chave]
            p.write_text('12:34:56', encoding='utf-8')
            os.utime(p, (99999, 99999))
        self.assertEqual([s['estado'] for s in servicos.ler(self.ctx)['servicos'][2:]], ['ok', 'ok'])
        self.ctx['agora'] += 120
        self.assertEqual([s['estado'] for s in servicos.ler(self.ctx)['servicos'][2:]], ['fora', 'fora'])

    def test_processos_csv(self):
        self.assertEqual(processos.ler({'executar': lambda a, **kw:
            '"claude.exe","1"\n"CLAUDE.EXE","2"\n"node.exe","3"\n"outro.exe","4"'}),
            {'claude.exe': 2, 'node.exe': 1})

    def test_processos_erro(self):
        def falha(*a, **kw):
            raise TimeoutError
        self.assertEqual(processos.ler({'executar': falha}), {})

    def test_batimento_bloqueado_tem_timeout(self):
        liberar = threading.Event()
        terminou = threading.Event()
        def bloqueado(*args):
            liberar.wait(5)
            terminou.set()
            return (99999, '12:34:56')
        try:
            with patch.object(servicos, '_ler_batimento', side_effect=bloqueado):
                inicio = time.monotonic()
                self.assertIsNone(servicos._batimento_limitado('arquivo-sintetico', True))
                self.assertLess(time.monotonic() - inicio, 2)
        finally:
            liberar.set()
            terminou.wait(2)
