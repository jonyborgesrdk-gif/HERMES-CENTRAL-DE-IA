"""Hosts HTTPS opt-in, com servidor efêmero e sem coletor real."""
import http.client
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import servidor

HOST = 'desktop-a6kd752.tail03a452.ts.net'


class HostsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.publico = Path(self.tmp.name)
        (self.publico / 'index.html').write_text('<h1>Exemplo</h1>', encoding='utf-8')
        (self.publico / 'dados.js').write_text(servidor.PREFIXO + '{"agentes": []});', encoding='utf-8')

    def iniciar(self, valor):
        with patch.dict(os.environ):
            os.environ.pop('PAINEL_HOSTS', None)
            if valor is not None:
                os.environ['PAINEL_HOSTS'] = valor
            # Reserva sem listen: conexão recusada sem alcançar o coletor real.
            reserva = socket.socket()
            reserva.bind(('127.0.0.1', 0))
            self.addCleanup(reserva.close)
            srv = servidor.PainelHTTP(self.publico, 0, porta_coletor=reserva.getsockname()[1])
        fio = threading.Thread(target=srv.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        fio.start()
        self.addCleanup(self.encerrar, srv, fio)
        return srv

    def encerrar(self, srv, fio):
        srv.parar.set()
        srv.shutdown()
        srv.server_close()
        fio.join(2)

    def pedir(self, srv, host, origin=None, caminho='/', metodo='GET', **headers):
        headers['Host'] = host
        if origin is not None:
            headers['Origin'] = origin
        conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=4)
        try:
            conn.request(metodo, caminho, headers=headers)
            resposta = conn.getresponse()
            resposta.read()
            return resposta.status
        finally:
            conn.close()

    def test_sem_variavel_host_ts_net_e_403(self):
        self.assertEqual(self.pedir(self.iniciar(None), HOST), 403)

    def test_host_opt_in_aceita_raiz(self):
        srv = self.iniciar(HOST)
        self.assertEqual(self.pedir(srv, HOST), 200)

    def test_host_com_porta_443_aceita(self):
        self.assertEqual(self.pedir(self.iniciar(HOST), HOST + ':443'), 200)

    def test_host_com_maiusculas_aceita(self):
        self.assertEqual(self.pedir(self.iniciar(HOST), HOST.upper()), 200)

    def test_outro_host_ts_net_continua_403(self):
        self.assertEqual(self.pedir(self.iniciar(HOST), 'outro.tail03a452.ts.net'), 403)

    def test_origin_https_do_host_aceita_dados(self):
        srv = self.iniciar(HOST)
        status = self.pedir(srv, HOST, 'https://' + HOST, '/dados.json', **{'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(status, 200)

    def test_origin_http_do_host_e_403(self):
        srv = self.iniciar(HOST)
        self.assertEqual(self.pedir(srv, HOST, 'http://' + HOST), 403)

    def test_origin_https_de_outro_host_e_403(self):
        srv = self.iniciar(HOST)
        self.assertEqual(self.pedir(srv, HOST, 'https://outro.tail03a452.ts.net'), 403)

    def test_refresh_passa_da_checagem_de_origem(self):
        srv = self.iniciar(HOST)
        status = self.pedir(srv, HOST, 'https://' + HOST, '/refresh', 'POST', **{'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(status, 503)

    def test_entradas_invalidas_sao_ignoradas(self):
        srv = self.iniciar('*,h.ts.net:443,https://h.ts.net,1.2.3.4,semponto')
        self.assertEqual(self.pedir(srv, 'h.ts.net'), 403)

    def test_ler_hosts_extra_rejeita_entradas_invalidas(self):
        for valor in ('*', 'h.ts.net:443', 'https://h.ts.net', '1.2.3.4',
                      'semponto', '-a.ts.net', 'a..ts.net', 'h.ts.net/x', 'h.123'):
            with self.subTest(valor=valor):
                self.assertEqual(servidor.ler_hosts_extra(valor), frozenset())

    def test_ler_hosts_extra_normaliza_nomes_validos(self):
        self.assertEqual(servidor.ler_hosts_extra(' OK.ts.net. , b.ts.net'),
                         frozenset({'ok.ts.net', 'b.ts.net'}))

    def test_ip_na_variavel_continua_403(self):
        self.assertEqual(self.pedir(self.iniciar('1.2.3.4'), '1.2.3.4'), 403)

    def test_loopback_continua_aceito_com_variavel(self):
        srv = self.iniciar(HOST)
        self.assertEqual(self.pedir(srv, '127.0.0.1:%d' % srv.server_port), 200)


if __name__ == '__main__':
    unittest.main()
