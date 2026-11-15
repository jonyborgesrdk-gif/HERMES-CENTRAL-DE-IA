"""Regressões HTTP/SSE isoladas: loopback efêmero e dados sintéticos."""
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import servidor


class HttpAuditoria(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.publico = Path(self.tmp.name)
        (self.publico / 'index.html').write_text('<h1>teste</h1>', encoding='utf-8')
        (self.publico / 'dados.js').write_text(
            servidor.PREFIXO + '{"numero": 1});', encoding='utf-8')
        self.server = servidor.PainelHTTP(self.publico, 0)
        self.fio = threading.Thread(target=self.server.serve_forever,
                                    kwargs={'poll_interval': 0.01}, daemon=True)
        self.fio.start()
        self.addCleanup(self.encerrar)

    def encerrar(self):
        self.server.parar.set()
        self.server.shutdown()
        self.server.server_close()
        self.fio.join(2)

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            conn.request('GET', path, headers=headers or {})
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            conn.close()

    @unittest.skipUnless(os.name == 'nt', 'aliases de caminho Win32/NTFS')
    def test_aliases_windows_nao_contornam_bloqueio_cross_site(self):
        for path in ('/dados.js.', '/dados.js%20', '/dados.js::$DATA', '/dados.js%3A%3A$DATA'):
            with self.subTest(path=path):
                status, _, corpo = self.get(path, {'Sec-Fetch-Site': 'cross-site'})
                self.assertEqual(status, 403)
                self.assertNotIn(b'numero', corpo)


class SseAuditoria(unittest.TestCase):
    def test_mudanca_de_conteudo_com_mesmo_mtime_e_tamanho(self):
        # Não depende da resolução do relógio do filesystem nem de sleeps:
        # duas publicações diferentes podem compartilhar a assinatura stat.
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / 'dados.js'
            arquivo.write_text(servidor.PREFIXO + '{"numero": 1});', encoding='utf-8')
            stat = arquivo.stat()
            parar = threading.Event()
            handler = object.__new__(servidor.Handler)
            handler.server = SimpleNamespace(publico=Path(pasta), parar=parar)
            handler.send_response = lambda *_: None
            handler.send_header = lambda *_: None
            handler.end_headers = lambda: None
            handler.wfile = io.BytesIO()
            voltas = []

            def esperar(_):
                if not voltas:
                    arquivo.write_text(servidor.PREFIXO + '{"numero": 2});', encoding='utf-8')
                    os.utime(arquivo, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                    self.assertEqual(arquivo.stat().st_mtime_ns, stat.st_mtime_ns)
                    self.assertEqual(arquivo.stat().st_size, stat.st_size)
                else:
                    parar.set()
                voltas.append(1)

            with patch.object(parar, 'wait', side_effect=esperar):
                handler.eventos()
            eventos = [json.loads(linha[6:]) for linha in handler.wfile.getvalue().splitlines()
                       if linha.startswith(b'data: ')]
            self.assertEqual([d['numero'] for d in eventos], [1, 2])


if __name__ == '__main__':
    unittest.main()
