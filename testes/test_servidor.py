"""Servidor em loopback efêmero, dados sintéticos e coletor sempre falso."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import servidor


def setUpModule():
    for nome in ('Popen', 'run'):
        guarda = patch.object(subprocess, nome, side_effect=AssertionError('coletor real proibido'))
        guarda.start()
        unittest.addModuleCleanup(guarda.stop)


class ServidorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.publico = Path(self.tmp.name) / 'publico'
        self.publico.mkdir()
        (self.publico / 'index.html').write_text('<h1>Exemplo</h1>', encoding='utf-8')
        self.publicar(1)
        self.server = servidor.PainelHTTP(self.publico, 0)
        self.fio = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.fio.start()
        self.addCleanup(self.encerrar)

    def encerrar(self):
        self.server.parar.set()
        self.server.shutdown()
        self.server.server_close()
        self.fio.join(2)

    def publicar(self, numero):
        (self.publico / 'dados.js').write_text(servidor.PREFIXO + json.dumps(
            {'origem': 'vivo', 'numero': numero, 'agentes': []}) + ');\n', encoding='utf-8')

    def conexao(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        self.addCleanup(conn.close)
        return conn

    def get(self, path, headers=None):
        conn = self.conexao()
        conn.request('GET', path, headers=headers or {})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def evento(self, resposta):
        evento = None
        for _ in range(10):
            linha = resposta.readline().decode('utf-8').strip()
            if linha.startswith('event: '):
                evento = linha[7:]
            if linha.startswith('data: '):
                self.assertEqual(evento, 'dados')
                return json.loads(linha[6:])
        self.fail('evento dados não recebido')

    def test_raiz_sem_cache(self):
        status, headers, corpo = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Exemplo', corpo)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_json_extraido_do_js(self):
        status, headers, corpo = self.get('/dados.json')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(corpo)['origem'], 'vivo')
        self.assertIn('application/json', headers['Content-Type'])

    def test_sse_primeiro_evento_e_mudanca(self):
        conn = self.conexao()
        conn.request('GET', '/eventos')
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.readline().strip(), b'retry: 2000')
        self.assertEqual(self.evento(r)['numero'], 1)
        self.publicar(2)
        self.assertEqual(self.evento(r)['numero'], 2)
        r.close()

    def test_sse_aguarda_primeira_publicacao(self):
        (self.publico / 'dados.js').unlink()
        conn = self.conexao()
        conn.request('GET', '/eventos')
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.readline().strip(), b'retry: 2000')
        self.publicar(3)
        self.assertEqual(self.evento(r)['numero'], 3)
        r.close()

    def test_traversal_e_backslash(self):
        (Path(self.tmp.name) / 'x').write_text('privado', encoding='utf-8')
        for path in ('/../x', '/%2e%2e/x', '/..%5cx', '/%00'):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 403)

    def test_sem_listagem(self):
        (self.publico / 'pasta').mkdir()
        self.assertEqual(self.get('/pasta/')[0], 404)

    def test_origin_estranho_e_host(self):
        self.assertEqual(self.get('/dados.json', {'Origin': 'http://exemplo.invalid'})[0], 403)
        self.assertEqual(self.get('/', {'Host': 'exemplo.invalid'})[0], 403)

    def test_origin_proprio(self):
        origin = 'http://127.0.0.1:%d' % self.server.server_port
        self.assertEqual(self.get('/dados.json', {'Origin': origin})[0], 200)

    def test_json_invalido_nao_executado(self):
        (self.publico / 'dados.js').write_text('alert("invalido")', encoding='utf-8')
        self.assertEqual(self.get('/dados.json')[0], 503)

    def test_arquivo_estatico(self):
        (self.publico / 'teste.css').write_text('body{}', encoding='utf-8')
        self.assertEqual(self.get('/teste.css?versao=1')[2], b'body{}')


class SupervisorTests(unittest.TestCase):
    def test_cliente_resetado_fecha_sem_excecao(self):
        handler = object.__new__(servidor.Handler)
        with patch.object(servidor.BaseHTTPRequestHandler, 'handle', side_effect=ConnectionResetError):
            handler.handle()

    def test_trava_unica_e_liberacao(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / 'servidor.trava'
            trava = servidor.Trava(caminho)
            try:
                with self.assertRaisesRegex(RuntimeError, 'já existe'):
                    servidor.Trava(caminho)
            finally:
                trava.close()
            servidor.Trava(caminho).close()

    def test_supervisor_para_filho_mock(self):
        iniciado = threading.Event()
        proc = Mock()
        proc.poll.return_value = None
        def popen(*a, **kw):
            iniciado.set()
            return proc
        with patch.object(subprocess, 'Popen', side_effect=popen) as criar:
            s = servidor.Supervisor(['--sem-gh'], job_factory=lambda: None)
            s.fio.start()
            self.assertTrue(iniciado.wait(2))
            s.close()
            proc.terminate.assert_called_once()
            proc.wait.assert_called_once_with(timeout=3)
            comando = criar.call_args.args[0]
            self.assertEqual(comando[0], sys.executable)
            self.assertIn('--vivo', comando)
            self.assertIn('--sem-gh', comando)

    def test_supervisor_espera_tres_segundos_apos_queda(self):
        proc = Mock()
        proc.poll.return_value = 1
        s = servidor.Supervisor(popen=Mock(return_value=proc), job_factory=lambda: None)
        esperas = []
        def esperar(segundos):
            esperas.append(segundos)
            if segundos == 3:
                s.parar.set()
            return s.parar.is_set()
        with patch.object(s.parar, 'wait', side_effect=esperar):
            s.rodar()
        self.assertIn(3, esperas)
