# -*- coding: utf-8 -*-
"""Testes do servidor HTTP do coletor (gatilho /refresh)."""
import importlib.util
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import comum  # noqa: E402

from importlib.machinery import SourceFileLoader
coletor = SourceFileLoader("coletor", str(comum.RAIZ / "coletor")).load_module()


class TestServidorRefresh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Acha uma porta livre para o teste
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        cls.porta = s.getsockname()[1]
        s.close()

        cls.evento = threading.Event()
        cls.servidor = coletor.ServidorRefresh(("127.0.0.1", cls.porta), coletor.HandlerRefresh)
        cls.servidor.evento_passada = cls.evento
        cls.servidor.estado_prs = None
        cls.servidor.ultimo_refresh = 0.0
        cls.servidor.trava_trigger = threading.Lock()
        cls.fio = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.fio.start()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def test_post_refresh_retorna_204(self):
        url = "http://127.0.0.1:%d/refresh" % self.porta
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 204)
        self.assertTrue(self.evento.is_set())
        self.evento.clear()

    def test_get_refresh_retorna_405(self):
        url = "http://127.0.0.1:%d/refresh" % self.porta
        req = urllib.request.Request(url, method="GET")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 405)

    def test_caminho_inexistente_retorna_404(self):
        url = "http://127.0.0.1:%d/outro" % self.porta
        req = urllib.request.Request(url, method="GET")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 404)

        req_post = urllib.request.Request(url, data=b"", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_post)
        self.assertEqual(ctx.exception.code, 404)


def porta_livre():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


class TestColetorVivoRefresh(unittest.TestCase):
    """B1: no modo --vivo o coletor também escuta POST /refresh e acorda o laço, sem refresh-completo."""

    def test_listener_vivo_204_acorda_e_nao_roda_refresh_completo(self):
        evento = threading.Event()
        porta = porta_livre()
        servidor = coletor.subir_servidor_refresh(porta, evento, None, modo_vivo=True)
        self.assertIsNotNone(servidor)
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)
        self.assertEqual(servidor.server_address[0], "127.0.0.1")
        with patch.object(coletor.subprocess, "run") as rodar:
            req = urllib.request.Request("http://127.0.0.1:%d/refresh" % porta, data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=3) as resp:
                self.assertEqual(resp.status, 204)
            rodar.assert_not_called()
        self.assertTrue(evento.is_set())

    def test_listener_vivo_mantem_limite_de_10_s(self):
        evento = threading.Event()
        porta = porta_livre()
        servidor = coletor.subir_servidor_refresh(porta, evento, None, modo_vivo=True)
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)
        url = "http://127.0.0.1:%d/refresh" % porta
        urllib.request.urlopen(urllib.request.Request(url, data=b"", method="POST"), timeout=3).close()
        evento.clear()
        urllib.request.urlopen(urllib.request.Request(url, data=b"", method="POST"), timeout=3).close()
        self.assertFalse(evento.is_set(), "segundo POST dentro de 10 s não acorda de novo")

    def test_modo_nao_vivo_continua_rodando_refresh_completo(self):
        evento = threading.Event()
        porta = porta_livre()
        servidor = coletor.subir_servidor_refresh(porta, evento, None)
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)
        with patch.object(coletor.subprocess, "run") as rodar:
            urllib.request.urlopen(urllib.request.Request(
                "http://127.0.0.1:%d/refresh" % porta, data=b"", method="POST"), timeout=3).close()
            rodar.assert_called_once()
            self.assertIn("refresh-completo", str(rodar.call_args.args[0][-1]))
            self.assertIn("creationflags", rodar.call_args.kwargs)    # sem_janela_kwargs

    def test_porta_ocupada_nao_derruba_o_coletor(self):
        ocupada = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ocupada.bind(("127.0.0.1", 0))
        ocupada.listen(1)
        self.addCleanup(ocupada.close)
        with patch("sys.stderr"):
            self.assertIsNone(coletor.subir_servidor_refresh(
                ocupada.getsockname()[1], threading.Event(), None, modo_vivo=True))

    def test_laco_vivo_acorda_pelo_evento_em_vez_de_dormir(self):
        capturado = {}
        chamadas = []

        def subir(porta, evento, estado_prs, modo_vivo=False):
            capturado.update(evento=evento, modo_vivo=modo_vivo, porta=porta)

        def passada(args, estado_prs, cache=None, overrides=None):
            chamadas.append(time.monotonic())
            if len(chamadas) == 2:
                raise KeyboardInterrupt
            threading.Timer(0.3, capturado["evento"].set).start()
            return {}

        with patch("sys.argv", ["coletor", "--vivo", "--sem-gh", "--intervalo", "3600"]),              patch.object(coletor.comum, "ARQ_AGENTES", Path("inexistente.json")),              patch.object(coletor, "subir_servidor_refresh", side_effect=subir),              patch.object(coletor, "passada_viva", side_effect=passada),              patch.object(coletor, "resumo", return_value=""),              patch("sys.stdout"):
            inicio = time.monotonic()
            with self.assertRaises(KeyboardInterrupt):
                coletor.main()
        self.assertTrue(capturado["modo_vivo"])
        self.assertEqual(capturado["porta"], coletor.PORTA_PADRAO)
        self.assertEqual(len(chamadas), 2)
        self.assertLess(time.monotonic() - inicio, 5, "o laço esperou o intervalo inteiro em vez de acordar")

    def test_pagina_chama_refresh_na_mesma_origem(self):
        html = (comum.RAIZ / "publico" / "classico.html").read_text(encoding="utf-8")
        self.assertNotIn("7420", html)
        self.assertIn('fetch("/refresh", { method: "POST"', html)
        self.assertIn("atualização falhou", html)
        self.assertNotIn("no-cors", html)

    def test_listener_recusa_origin_e_sec_fetch_site_com_403(self):
        for cabecalho, valor in (("Origin", "http://evil.example"), ("Origin", "null"),
                                 ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Site", "same-origin")):
            with self.subTest(cabecalho=cabecalho, valor=valor):
                evento = threading.Event()
                porta = porta_livre()
                servidor = coletor.subir_servidor_refresh(porta, evento, None, modo_vivo=True)
                self.addCleanup(servidor.server_close)
                self.addCleanup(servidor.shutdown)
                req = urllib.request.Request("http://127.0.0.1:%d/refresh" % porta, data=b"", method="POST",
                                             headers={cabecalho: valor})
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=3)
                self.assertEqual(ctx.exception.code, 403)
                ctx.exception.close()
                self.assertFalse(evento.is_set(), "POST recusado não pode acordar o laço")
                # Sem esses cabeçalhos (como o proxy do servidor.py) continua 204.
                with urllib.request.urlopen(urllib.request.Request(
                        "http://127.0.0.1:%d/refresh" % porta, data=b"", method="POST"), timeout=3) as resp:
                    self.assertEqual(resp.status, 204)

    def test_handler_tem_timeout_e_conexao_pendurada_nao_trava_o_listener(self):
        self.assertEqual(coletor.HandlerRefresh.timeout, 5)
        with patch.object(coletor.HandlerRefresh, "timeout", 0.5):
            evento = threading.Event()
            porta = porta_livre()
            servidor = coletor.subir_servidor_refresh(porta, evento, None, modo_vivo=True)
            self.addCleanup(servidor.server_close)
            self.addCleanup(servidor.shutdown)
            pendurada = socket.create_connection(("127.0.0.1", porta), timeout=3)
            self.addCleanup(pendurada.close)
            pendurada.sendall(b"POST /refresh HTTP/1.1\r\nHost: x\r\n")    # cabeçalhos nunca terminam
            inicio = time.monotonic()
            with urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:%d/refresh" % porta, data=b"", method="POST"), timeout=5) as resp:
                self.assertEqual(resp.status, 204)
            self.assertLess(time.monotonic() - inicio, 4)
            self.assertTrue(evento.is_set())


if __name__ == "__main__":
    unittest.main()
