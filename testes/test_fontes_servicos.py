import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
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

    def test_vigia_json_ts_prevalece_sobre_mtime(self):
        p = self.ctx['vigia_path']
        p.write_text(json.dumps({'pid': 1234, 'hora': '2026-01-01T12:34:56-03:00',
                                'ts': 99999}), encoding='utf-8')
        os.utime(p, (10, 10))
        vigia = servicos.ler(self.ctx)['servicos'][2]
        self.assertEqual((vigia['estado'], vigia['detalhe']), ('ok', 'pid 1234 · 01/01 12:34:56'))

    def _vigia_em_memoria(self, data, agora, ts=None):
        self.ctx['agora'] = agora.timestamp()
        texto = json.dumps({'pid': 1823, 'hora': data,
                            'ts': ts if ts is not None else datetime.fromisoformat(data).timestamp()})
        with patch.object(Path, 'read_text', return_value=texto), \
                patch.object(Path, 'stat', return_value=SimpleNamespace(st_mtime=self.ctx['agora'])), \
                patch.object(servicos.time, 'monotonic', return_value=10):
            r = servicos.ler(self.ctx)['servicos'][2]
        self.assertEqual(r['nome'], 'Vigia')
        self.assertEqual(r['hora'], self.ctx['agora'])
        return r

    def test_vigia_hoje_detalhe_preservado(self):
        agora = datetime.fromisoformat('2026-11-12T23:59:00-03:00')
        r = self._vigia_em_memoria('2026-11-12T23:58:42-03:00', agora)
        self.assertEqual((r['estado'], r['detalhe']), ('ok', 'pid 1823 · 23:58:42'))

    def test_vigia_16_dias_data_e_idade(self):
        agora = datetime.fromisoformat('2026-11-12T23:59:00-03:00')
        r = self._vigia_em_memoria('2026-10-27T23:58:42-03:00', agora)
        self.assertEqual(r['estado'], 'fora')
        self.assertEqual(r['detalhe'], 'parado desde 27/10 23:58 (há 16 d) · pid 1823')

    def test_vigia_velho_minutos_e_horas(self):
        agora = datetime.fromisoformat('2026-11-12T23:59:00-03:00')
        for atraso, esperado in ((timedelta(minutes=7), '7 min'), (timedelta(hours=3), '3 h')):
            with self.subTest(esperado=esperado):
                data = agora - atraso
                r = self._vigia_em_memoria(data.isoformat(), agora)
                self.assertEqual(r['estado'], 'fora')
                self.assertEqual(r['detalhe'],
                                 f'parado desde {data:%d/%m %H:%M} (há {esperado}) · pid 1823')

    def test_vigia_recente_dia_anterior_tem_data(self):
        agora = datetime.fromisoformat('2026-11-13T00:00:10-03:00')
        r = self._vigia_em_memoria('2026-11-12T23:59:42-03:00', agora)
        self.assertEqual((r['estado'], r['detalhe']), ('ok', 'pid 1823 · 12/11 23:59:42'))

    def test_vigia_data_invalida_sem_leitura(self):
        agora = datetime.fromisoformat('2026-11-12T23:59:00-03:00')
        r = self._vigia_em_memoria('data-invalida', agora, ts=agora.timestamp())
        self.assertEqual(r['estado'], 'sem leitura')

    def test_vigia_conversoes_protegidas(self):
        agora = datetime.fromisoformat('2026-11-12T23:59:00-03:00')
        for erro in (ValueError, OverflowError, OSError):
            with self.subTest(erro=erro), patch.object(servicos, 'datetime') as relogio:
                relogio.fromisoformat.side_effect = erro('data inválida')
                r = self._vigia_em_memoria(agora.isoformat(), agora, ts=agora.timestamp())
                self.assertEqual(r['estado'], 'sem leitura')

    def test_vigia_json_velho_nao_usa_mtime_recente(self):
        p = self.ctx['vigia_path']
        p.write_text(json.dumps({'pid': 1234, 'hora': '2026-01-01T12:34:56-03:00',
                                'ts': 99880}), encoding='utf-8')
        os.utime(p, (99999, 99999))
        self.assertEqual(servicos.ler(self.ctx)['servicos'][2]['estado'], 'fora')

    def test_vigia_tres_segundos_no_futuro(self):
        self.ctx['vigia_path'].write_text(json.dumps({'pid': 1234,
            'hora': '2026-01-01T12:34:56-03:00', 'ts': 100003}), encoding='utf-8')
        self.assertEqual(servicos.ler(self.ctx)['servicos'][2]['estado'], 'ok')

    def test_batimento_novo_durante_health_checks(self):
        self.ctx['vigia_path'].write_text(json.dumps({'pid': 1234,
            'hora': '2026-01-01T12:34:56-03:00', 'ts': 100002}), encoding='utf-8')
        with patch.object(servicos.time, 'monotonic', side_effect=[10, 13]):
            self.assertEqual(servicos.ler(self.ctx)['servicos'][2]['estado'], 'ok')

    def test_vigia_json_invalido_e_erro_exato_arquivo(self):
        p = self.ctx['vigia_path']
        p.write_text('{"ts":"invalido","pid":1234}', encoding='utf-8')
        r = servicos.ler(self.ctx)['servicos'][2]
        self.assertEqual(r['estado'], 'sem leitura')
        self.assertIn('ValueError', r['detalhe'])
        p.unlink()
        self.assertIn('FileNotFoundError', servicos.ler(self.ctx)['servicos'][2]['detalhe'])

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
