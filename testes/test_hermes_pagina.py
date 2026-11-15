"""Páginas servidas pelo painel: / é a Central de operações (Hermes), /classico é a página anterior."""
import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import servidor

PUBLICO = RAIZ / 'publico'
DEMONSTRACAO = ('Ambiente de demonstração', 'Dados de demonstração', 'Valores ilustrativos', 'custo de exemplo',
                'Verificação de exemplo', 'Processos de exemplo', 'Prévia em HTML', 'Dados fictícios',
                'Baixar painel HTML', 'Workspace pessoal', 'Jony')


class PaginasTests(unittest.TestCase):
    def setUp(self):
        self.server = servidor.PainelHTTP(PUBLICO, 0)
        self.fio = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.fio.start()
        self.addCleanup(self.encerrar)

    def encerrar(self):
        self.server.parar.set()
        self.server.shutdown()
        self.server.server_close()
        self.fio.join(2)

    def pedir(self, metodo, caminho, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        self.addCleanup(conn.close)
        conn.request(metodo, caminho, headers=headers or {})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_raiz_serve_a_pagina_nova(self):
        status, headers, corpo = self.pedir('GET', '/')
        self.assertEqual(status, 200)
        self.assertEqual(corpo, (PUBLICO / 'index.html').read_bytes())
        texto = corpo.decode('utf-8')
        self.assertIn('Hermes — Central de operações', texto)
        self.assertIn('src="hermes.js"', texto)
        self.assertIn('href="/classico"', texto)
        self.assertNotIn('id="linhas"', texto)         # a tabela da página clássica não está aqui
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_classico_serve_a_pagina_antiga_completa(self):
        status, headers, corpo = self.pedir('GET', '/classico')
        self.assertEqual(status, 200)
        self.assertEqual(corpo, (PUBLICO / 'classico.html').read_bytes())
        texto = corpo.decode('utf-8')
        for marca in ('id="linhas"', 'id="refresh"', 'id="privacidade"', 'id="tema"', 'cartao-apoio.js', 'new ambiente.EventSource',
                      'fetch("/refresh", { method: "POST"', 'href="/"'):
            self.assertIn(marca, texto)
        self.assertNotIn('hermes.js', texto)
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        self.assertEqual(self.pedir('GET', '/classico/')[0], 200)
        status, _, corpo_head = self.pedir('HEAD', '/classico')
        self.assertEqual((status, corpo_head), (200, b''))

    def test_csp_identica_nas_duas_paginas_e_nos_ativos(self):
        esperado = "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        for caminho in ('/', '/classico', '/hermes.js', '/hermes.css'):
            _, headers, _ = self.pedir('GET', caminho)
            self.assertEqual(headers['Content-Security-Policy'], esperado, caminho)
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff', caminho)
            self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin', caminho)
        self.assertNotIn('unsafe', Path(servidor.__file__).read_text(encoding='utf-8').split('Content-Security-Policy')[1][:160])

    def test_ativos_da_pagina_nova(self):
        status, headers, corpo = self.pedir('GET', '/hermes.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        self.assertEqual(corpo, (PUBLICO / 'hermes.js').read_bytes())
        status, headers, _ = self.pedir('GET', '/hermes.css')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/css'))

    def test_host_estranho_continua_recusado_na_pagina_nova_e_na_classica(self):
        for caminho in ('/', '/classico'):
            self.assertEqual(self.pedir('GET', caminho, {'Host': 'evil.example'})[0], 403)

    def test_pagina_nova_sem_texto_de_demonstracao(self):
        for nome in ('index.html', 'hermes.js', 'hermes.css'):
            texto = (PUBLICO / nome).read_text(encoding='utf-8')
            for proibido in DEMONSTRACAO:
                self.assertNotIn(proibido, texto, '%s: %s' % (nome, proibido))

    def test_pagina_nova_sem_url_externa_nem_html_dinamico(self):
        for nome in ('index.html', 'hermes.js', 'hermes.css'):
            texto = (PUBLICO / nome).read_text(encoding='utf-8')
            texto = re.sub(r'http://www\.w3\.org/2000/svg', '', texto)
            texto = texto.replace('http://127.0.0.1:7421/', '')    # endereço local do próprio painel (aviso de file:), não é externo
            self.assertIsNone(re.search(r'https?://', texto), nome)
        js = (PUBLICO / 'hermes.js').read_text(encoding='utf-8')
        for api in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval('):
            self.assertNotIn(api, js)

    def test_fixture_atravessa_o_servidor_e_mantem_o_contrato(self):
        fixture = json.loads((RAIZ / 'testes' / 'fixtures' / 'dados-hermes.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            for nome in ('index.html', 'classico.html', 'hermes.js', 'hermes.css'):
                (pasta / nome).write_bytes((PUBLICO / nome).read_bytes())
            (pasta / 'dados.js').write_text(servidor.PREFIXO + json.dumps(fixture) + ');\n', encoding='utf-8')
            server = servidor.PainelHTTP(pasta, 0)
            fio = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
            fio.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                conn.request('GET', '/dados.json')
                r = conn.getresponse()
                self.assertEqual(r.status, 200)
                self.assertEqual(json.loads(r.read()), fixture)
                conn.close()
            finally:
                server.parar.set()
                server.shutdown()
                server.server_close()
                fio.join(2)


if __name__ == '__main__':
    unittest.main()
