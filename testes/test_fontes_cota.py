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
        self.assertEqual(self.calls[0], (['claude', 'auth', 'status', '--json'], 10))
        self.assertEqual(self.calls[1], (['claude', '--safe-mode', '--no-session-persistence',
                                        '-p', '/usage', '--output-format', 'stream-json', '--verbose'], 60))

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
        self.assertEqual(self.calls[0], (['claude', 'auth', 'status', '--json'], 10))
        self.assertEqual(cota_claude.ler(self.ctx)['email'], 'conta@example.com')
        self.ctx['agora'] += 100
        cota_claude.atualizar(self.ctx)
        self.assertEqual(len(self.calls), 2)

    def _sem_login(self, codigo):
        anterior = dict(sessao_5h=17, semanal=31, reset_5h='reset anterior',
                        reset_semanal='reset semanal anterior', hora=99000,
                        email='anterior@example.com', plano='anterior')
        chamadas = []
        def executar(argv, timeout):
            chamadas.append((argv, timeout))
            texto = json.dumps({'loggedIn': False, 'authMethod': 'none', 'email': 'nao-herdar'})
            if codigo:
                raise subprocess.CalledProcessError(codigo, argv, output=texto)
            return texto
        self.ctx['executar'] = executar
        with patch.object(cota_claude, 'ler', return_value=anterior):
            r = cota_claude.atualizar(self.ctx)
        self.assertEqual(r['erro'], 'Claude Code sem login')
        self.assertEqual(r['erro_auth'], 'Claude Code sem login')
        for chave, valor in anterior.items():
            self.assertEqual(r[chave], valor)
        self.assertEqual(r['tentativa'], 100000)
        self.assertEqual(chamadas, [(['claude', 'auth', 'status', '--json'], 10)])
        self.assertEqual(cota_claude.ler(self.ctx), r)
        self.ctx['agora'] += 299
        self.assertEqual(cota_claude.atualizar(self.ctx), r)
        self.assertEqual(len(chamadas), 1)
        self.ctx['agora'] += 1
        cota_claude.atualizar(self.ctx)
        self.assertEqual(len(chamadas), 2)

    def test_sem_login_codigo_1_nao_chama_usage(self):
        self._sem_login(1)

    def test_sem_login_codigo_0_nao_chama_usage(self):
        self._sem_login(0)

    def test_login_bom_remove_erros_antigos(self):
        anterior = {'erro': 'Claude Code sem login', 'erro_auth': 'Claude Code sem login'}
        normal = self.executar
        def executar(argv, timeout):
            if argv[1:3] == ['auth', 'status']:
                self.calls.append((argv, timeout))
                return json.dumps({'loggedIn': True, 'email': 'conta@example.com',
                                   'subscriptionType': 'plano-teste'})
            return normal(argv, timeout)
        self.ctx['executar'] = executar
        with patch.object(cota_claude, 'ler', return_value=anterior):
            r = cota_claude.atualizar(self.ctx)
        self.assertNotIn('erro', r)
        self.assertNotIn('erro_auth', r)
        self.assertEqual(r['sessao_5h'], 6)
        self.assertEqual(self.calls[0], (['claude', 'auth', 'status', '--json'], 10))
        self.assertIn('/usage', self.calls[1][0])

    def test_identidade_invalida_ou_falha_nao_e_sem_login(self):
        normal = self.executar
        for codigo, texto in ((0, '{'), (1, '{'), (0, '[]'), (1, '{"loggedIn":true}'),
                              (1, '{"loggedIn":0}'), (1, '{"loggedIn":"false"}')):
            with self.subTest(codigo=codigo, texto=texto):
                self.calls.clear()
                def executar(argv, timeout):
                    if argv[1:3] == ['auth', 'status']:
                        self.calls.append((argv, timeout))
                        if codigo:
                            raise subprocess.CalledProcessError(codigo, argv, output=texto)
                        return texto
                    return normal(argv, timeout)
                self.ctx['executar'] = executar
                with patch.object(cota_claude, 'ler', return_value={'email': 'herdado', 'plano': 'herdado'}):
                    r = cota_claude.atualizar(self.ctx)
                self.assertEqual(r['erro_auth'], 'identidade Claude sem leitura')
                self.assertEqual((r['email'], r['plano']), ('herdado', 'herdado'))
                self.assertNotIn('erro', r)
                self.assertEqual(r['sessao_5h'], 6)
                self.assertIn('/usage', self.calls[1][0])

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
