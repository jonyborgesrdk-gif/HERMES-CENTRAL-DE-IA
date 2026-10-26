import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from lib.fontes import cota_claude


def setUpModule():
    for nome in ('run', 'Popen'):
        p = patch.object(subprocess, nome, side_effect=AssertionError('subprocesso real proibido'))
        p.start()
        unittest.addModuleCleanup(p.stop)


USAGE = ('Current session: 6% used · resets Oct 27, 12:10am (America/Sao_Paulo)\n'
         'Current week (all models): 42% used · resets Oct 30, 1am\n'
         'Current week (Sonnet only): 88% used · resets outra hora')


class CotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.ctx = {'dir': self.tmp.name, 'agora': 100000, 'executar': self.executar}

    def executar(self, argv, timeout):
        self.calls.append((argv, timeout))
        return json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': USAGE}]}})

    def test_percentuais(self):
        r = cota_claude.ler_usage(USAGE)
        self.assertEqual((r['sessao_5h'], r['semanal']), (6, 42))

    def test_reset_literal(self):
        self.assertEqual(cota_claude.ler_usage(USAGE)['reset_5h'],
                         'Oct 27, 12:10am (America/Sao_Paulo)')

    def test_ausente(self):
        self.assertIsNone(cota_claude.ler_usage('indisponivel')['semanal'])

    def test_atualizar_cache(self):
        r = cota_claude.atualizar(self.ctx)
        self.assertEqual(r['sessao_5h'], 6)
        self.assertEqual(cota_claude.ler(self.ctx), r)
        self.assertEqual(self.calls[0][1], 60)
        self.assertIn('--safe-mode', self.calls[0][0])

    def test_cinco_minutos(self):
        cota_claude.atualizar(self.ctx)
        self.ctx['agora'] += 299
        cota_claude.atualizar(self.ctx)
        self.assertEqual(len(self.calls), 1)
        self.ctx['agora'] += 1
        cota_claude.atualizar(self.ctx)
        self.assertEqual(len(self.calls), 2)

    def test_falha_preserva_cache_e_limita_retentativa(self):
        cota_claude.atualizar(self.ctx)
        def falha(*args, **kwargs):
            raise TimeoutError
        self.ctx.update(agora=100300, executar=falha)
        r = cota_claude.atualizar(self.ctx)
        self.assertEqual(r['sessao_5h'], 6)
        self.assertEqual(r['hora'], 100000)
        self.assertIn('erro', r)
        self.ctx.update(agora=100301, executar=self.executar)
        cota_claude.atualizar(self.ctx)
        self.assertEqual(len(self.calls), 1)

    def test_cache_corrompido(self):
        (Path(self.tmp.name) / 'cota-claude.json').write_text('{', encoding='utf-8')
        self.assertEqual(cota_claude.ler(self.ctx), {})
