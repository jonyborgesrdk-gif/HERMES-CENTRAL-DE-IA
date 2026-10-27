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
        if argv[1:3] == ['auth', 'status']:
            return json.dumps({'email': 'conta@example.com', 'subscriptionType': 'plano-teste'})
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
        self.assertEqual(len(self.calls), 2)
        self.ctx['agora'] += 1
        cota_claude.atualizar(self.ctx)
        self.assertEqual(len(self.calls), 4)

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
        self.assertEqual(len(self.calls), 2)

    def test_cache_corrompido(self):
        (Path(self.tmp.name) / 'cota-claude.json').write_text('{', encoding='utf-8')
        self.assertEqual(cota_claude.ler(self.ctx), {})

    def test_usage_com_avisos_e_linhas_vazias(self):
        normal = self.executar
        self.ctx['executar'] = lambda a, **kw: '\nAviso textual\n[]\n' + normal(a, **kw) + '\n\n'
        r = cota_claude.atualizar(self.ctx)
        self.assertEqual(r['sessao_5h'], 6)
        self.assertNotIn('erro', r)

    def test_tentativa_nao_numerica(self):
        for valor in ('corrompido', None, {}, True):
            with self.subTest(valor=valor):
                (Path(self.tmp.name) / 'cota-claude.json').write_text(
                    json.dumps({'tentativa': valor}), encoding='utf-8')
                r = cota_claude.atualizar(self.ctx)
                self.assertEqual(r['sessao_5h'], 6)
                self.assertNotIn('erro', r)

    def test_auth_status_no_mesmo_cache(self):
        r = cota_claude.atualizar(self.ctx)
        self.assertEqual((r['email'], r['plano']), ('conta@example.com', 'plano-teste'))
        self.assertEqual(self.calls[1], (['claude', 'auth', 'status', '--json'], 10))
        self.assertEqual(cota_claude.ler(self.ctx)['email'], 'conta@example.com')
        self.ctx['agora'] += 100
        cota_claude.atualizar(self.ctx)
        self.assertEqual(len(self.calls), 2)

    def test_auth_falha_nao_apaga_cota_e_preserva_identidade(self):
        cota_claude.atualizar(self.ctx)
        normal = self.executar
        def executar(argv, **kw):
            if argv[1:3] == ['auth', 'status']:
                raise TimeoutError
            return normal(argv, **kw)
        self.ctx.update(executar=executar, agora=100300)
        r = cota_claude.atualizar(self.ctx)
        self.assertEqual(r['sessao_5h'], 6)
        self.assertEqual(r['email'], 'conta@example.com')
        self.assertIn('erro_auth', r)
