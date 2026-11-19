"""E26a: listeners reais em loopback efêmero e somente dados sintéticos."""
from contextlib import redirect_stderr, redirect_stdout
import base64
import hashlib
import http.client
import io
import json
import logging
import os
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import servidor


FLAGS = ('PAINEL_CONEXOES', 'PAINEL_COMANDO', 'PAINEL_REDE_SESSAO')
HOST_TAILNET = 'painel-exemplo.tailnet-exemplo.ts.net'
SEM_LEITURA = b'{"estado":"sem leitura"}'


def pedir(srv, caminho='/', headers=None, metodo='GET'):
    conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=3)
    try:
        conn.request(metodo, caminho, headers=headers or {})
        resposta = conn.getresponse()
        return resposta.status, dict(resposta.getheaders()), resposta.read()
    finally:
        conn.close()


class BaseLocal(unittest.TestCase):
    def setUp(self):
        self.fios_anteriores = set(threading.enumerate())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        ambiente = {nome: '' for nome in FLAGS}
        ambiente.update(PAINEL_IAS_DIR=str(self.pasta), PAINEL_HOSTS=HOST_TAILNET,
                        PAINEL_PORTA_LOCAL='7422')
        self.env = patch.dict(os.environ, ambiente)
        self.env.start()
        self.addCleanup(self.env.stop)
        publico = self.pasta / 'publico'
        publico.mkdir()
        (publico / 'index.html').write_text('principal', encoding='utf-8')
        (publico / 'dados.js').write_text(servidor.PREFIXO + '{"origem":"teste"});', encoding='utf-8')
        self.principal = servidor.PainelHTTP(publico, 0)
        self.fio = threading.Thread(target=self.principal.serve_forever,
                                    kwargs={'poll_interval': 0.01})
        self.fio.start()
        self.locais = []
        self.addCleanup(self.encerrar)
        self.arquivo = self.pasta / 'local.json'

    def encerrar(self):
        self.principal.parar.set()
        self.principal.shutdown()
        self.principal.server_close()
        self.fio.join(3)
        self.assertFalse(self.fio.is_alive())
        for local in self.locais:
            local.fio.join(4)
            self.assertFalse(local.fio.is_alive())
            self.assertEqual(local.socket.fileno(), -1)
        self.assertEqual(set(threading.enumerate()) - self.fios_anteriores, set())

    def iniciar(self):
        # Porta 0 só na construção de teste; a configuração de produção é testada à parte.
        with patch.object(servidor, 'ler_porta_local', return_value=0):
            local = servidor.iniciar_servidor_local(self.principal)
        if local is not None:
            self.locais.append(local)
        return local

    def publicar(self, valor=None):
        self.arquivo.write_text(json.dumps(valor if valor is not None else {
            'gerado_em': '2026-11-19T12:00:00', 'comando': {'texto': 'SEGREDO_SINTETICO'}}),
            encoding='utf-8')


class AtivacaoLocal(BaseLocal):
    def test_desligado_nao_cria_socket_nem_log_e_preserva_principal(self):
        with patch.object(servidor, 'PainelLocalHTTP') as criar, \
                patch.object(servidor, 'ler_porta_local') as porta, \
                patch.object(logging.getLogger('painel.servidor'), 'info') as info, \
                patch.object(logging.getLogger('painel.servidor'), 'warning') as aviso:
            for valor in ('', '0', 'true', 'sim', '01', ' 1', '1 '):
                with patch.dict(os.environ, {nome: valor for nome in FLAGS}):
                    self.assertIsNone(self.iniciar())
            criar.assert_not_called()
            porta.assert_not_called()
            info.assert_not_called()
            aviso.assert_not_called()
        self.assertEqual(pedir(self.principal)[2], b'principal')
        self.assertEqual(json.loads(pedir(self.principal, '/dados.json')[2]), {'origem': 'teste'})
        self.assertEqual(pedir(self.principal, '/local.json')[0], 404)

    def test_cada_flag_exata_habilita_socket_loopback_e_json(self):
        self.publicar()
        for nome in FLAGS:
            with self.subTest(nome=nome), patch.dict(os.environ, {nome: '1'}):
                local = self.iniciar()
                self.assertIsNotNone(local)
                self.assertEqual(local.socket.getsockname()[0], '127.0.0.1')
                self.assertEqual(local.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    self.assertEqual(local.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
                self.assertIs(local.parar, self.principal.parar)
                self.assertTrue(local.fio.daemon)
                self.assertEqual(pedir(local)[0], 200)
                self.assertEqual(pedir(local, '/local.json')[0], 200)
                self.assertTrue(all(fio.daemon for fio in local._fios_requisicao))

    def test_porta_ocupada_nao_derruba_principal(self):
        for reutilizar in (0, 1):
            with self.subTest(SO_REUSEADDR=reutilizar), socket.socket() as reserva:
                reserva.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, reutilizar)
                reserva.bind(('127.0.0.1', 0))
                reserva.listen()
                with patch.dict(os.environ, PAINEL_COMANDO='1',
                                PAINEL_PORTA_LOCAL=str(reserva.getsockname()[1])), \
                        self.assertLogs('painel.servidor', level='WARNING') as logs:
                    self.assertIsNone(servidor.iniciar_servidor_local(self.principal))
                self.assertEqual(logs.output, [
                    'WARNING:painel.servidor:listener local indisponível: falha de bind'])
                self.assertEqual(pedir(self.principal)[0], 200)
                self.assertFalse(self.principal.parar.is_set())

    def test_segundo_listener_na_mesma_porta_falha_e_principal_segue(self):
        with patch.dict(os.environ, PAINEL_COMANDO='1'):
            local = self.iniciar()
        with self.assertRaises(OSError):
            segundo = servidor.PainelLocalHTTP(local.server_port, threading.Event())
            segundo.server_close()  # Mesmo se houver regressão, não vazar o socket do teste.
        self.assertEqual(pedir(local)[0], 200)
        self.assertEqual(pedir(self.principal)[0], 200)
        self.assertFalse(self.principal.parar.is_set())

    def test_diretorio_resolvido_dentro_de_publico_bloqueia_listener(self):
        for dados in (self.pasta / 'publico', self.pasta / 'publico' / 'estado',
                      self.pasta / 'publico' / '..' / 'publico' / 'estado'):
            with self.subTest(dados=dados), patch.object(servidor, 'RAIZ', self.pasta), \
                    patch.dict(os.environ, PAINEL_COMANDO='1', PAINEL_IAS_DIR=str(dados)), \
                    self.assertLogs('painel.servidor', level='WARNING') as logs:
                self.assertIsNone(self.iniciar())
            self.assertEqual(logs.output, [
                'WARNING:painel.servidor:listener local indisponível: diretório de dados dentro de publico'])
        self.assertEqual(pedir(self.principal)[0], 200)

    def test_porta_igual_a_principal_nao_tenta_bind(self):
        with patch.dict(os.environ, PAINEL_COMANDO='1',
                        PAINEL_PORTA_LOCAL=str(self.principal.server_port)), \
                patch.object(servidor, 'PainelLocalHTTP') as criar, \
                self.assertLogs('painel.servidor', level='WARNING') as logs:
            self.assertIsNone(servidor.iniciar_servidor_local(self.principal))
            criar.assert_not_called()
        self.assertEqual(logs.output, [
            'WARNING:painel.servidor:listener local indisponível: porta coincide com a principal'])
        self.assertEqual(pedir(self.principal)[0], 200)

    def test_raiz_publica_real_nao_serve_pagina_local_ou_traversal(self):
        principal = servidor.PainelHTTP(servidor.RAIZ / 'publico', 0)
        fio = threading.Thread(target=principal.serve_forever, kwargs={'poll_interval': 0.01})
        fio.start()
        try:
            for caminho, esperado in (('/publico_local/index.html', 404),
                                      ('/../publico_local/index.html', 403),
                                      ('/..%2fpublico_local/index.html', 403),
                                      ('/..%5cpublico_local/index.html', 403)):
                with self.subTest(caminho=caminho):
                    status, _, corpo = pedir(principal, caminho)
                    self.assertEqual(status, esperado)
                    self.assertNotIn('Painel local — dados sensíveis'.encode('utf-8'), corpo)
        finally:
            principal.parar.set()
            principal.shutdown()
            principal.server_close()
            fio.join(3)
        self.assertFalse(fio.is_alive())

    def test_evento_compartilhado_fecha_listener_e_handlers(self):
        with patch.dict(os.environ, PAINEL_COMANDO='1'):
            local = self.iniciar()
        self.assertEqual(pedir(local)[0], 200)
        self.principal.parar.set()
        local.fio.join(3)
        self.assertFalse(local.fio.is_alive())
        self.assertEqual(local.socket.fileno(), -1)
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', local.server_port), timeout=0.2)


class HttpLocal(BaseLocal):
    def setUp(self):
        super().setUp()
        os.environ['PAINEL_COMANDO'] = '1'
        self.local = self.iniciar()
        self.publicar()

    def test_pagina_minima_autocontida(self):
        status, headers, corpo = pedir(self.local)
        texto = corpo.decode('utf-8')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn('<title>Painel local — dados sensíveis</title>', texto)
        self.assertIn('Esta página só abre nesta máquina (127.0.0.1). Pode conter comandos, PIDs e endereços.', texto)
        self.assertIn("fetch('/local.json'", texto)
        self.assertIn('setInterval(atualizar, 5000)', texto)
        self.assertIn('textContent', texto)
        for proibido in ('innerHTML', '<script src=', '<link ', 'SEGREDO_SINTETICO'):
            self.assertNotIn(proibido, texto)

    def conferir_csp(self, csp, corpo):
        diretivas = dict(item.strip().split(' ', 1) for item in csp.split(';'))
        self.assertEqual(set(diretivas), {'default-src', 'connect-src', 'script-src', 'style-src',
                                         'frame-ancestors', 'base-uri', 'form-action'})
        for chave in ('default-src', 'frame-ancestors', 'base-uri', 'form-action'):
            self.assertEqual(diretivas[chave], "'none'")
        self.assertEqual(diretivas['connect-src'], "'self'")
        for tag in ('script', 'style'):
            bloco = corpo.split(('<%s>' % tag).encode('ascii'), 1)[1].split(
                ('</%s>' % tag).encode('ascii'), 1)[0]
            esperado = "'sha256-%s'" % base64.b64encode(hashlib.sha256(bloco).digest()).decode('ascii')
            self.assertEqual(diretivas[tag + '-src'], esperado)

    def test_csp_estrita_unica_na_pagina_com_hashes_do_corpo(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.local.server_port, timeout=3)
        try:
            conn.request('GET', '/')
            resposta = conn.getresponse()
            self.assertEqual(resposta.status, 200)
            corpo = resposta.read()
            politicas = resposta.headers.get_all('Content-Security-Policy')
            self.assertEqual(len(politicas), 1)
            self.conferir_csp(politicas[0], corpo)
        finally:
            conn.close()
        self.assertEqual(pedir(self.local, metodo='HEAD')[1]['Content-Security-Policy'], politicas[0])
        for srv, caminho in ((self.local, '/local.json'), (self.principal, '/')):
            csp = pedir(srv, caminho)[1]['Content-Security-Policy']
            self.assertEqual(csp, "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

    def test_csp_recalculada_do_arquivo_carregado_com_crlf(self):
        _, headers, corpo = pedir(self.local)
        pasta_html = self.pasta / 'publico_local'
        pasta_html.mkdir()
        alterado = corpo.replace(b'</script>', b'\r\n// script de teste\r\n</script>').replace(
            b'</style>', b'\r\n/* estilo de teste */\r\n</style>')
        (pasta_html / 'index.html').write_bytes(alterado)
        with patch.object(servidor, 'RAIZ', self.pasta):
            status, novos, recebido = pedir(self.local)
        self.assertEqual(status, 200)
        self.assertNotIn(b'\r', recebido)
        self.assertIn(b'// script de teste', recebido)
        self.assertNotEqual(headers['Content-Security-Policy'], novos['Content-Security-Policy'])
        self.conferir_csp(novos['Content-Security-Policy'], recebido)

    def test_json_so_do_diretorio_de_dados(self):
        status, headers, corpo = pedir(self.local, '/local.json')
        self.assertEqual(status, 200)
        self.assertIn('application/json', headers['Content-Type'])
        self.assertEqual(json.loads(corpo), json.loads(self.arquivo.read_text(encoding='utf-8')))

    def test_host_loopback_e_origin_exato(self):
        for nome in ('localhost', '127.0.0.1'):
            host = '%s:%d' % (nome, self.local.server_port)
            for caminho in ('/', '/local.json'):
                with self.subTest(host=host, caminho=caminho):
                    self.assertEqual(pedir(self.local, caminho, {'Host': host, 'Origin': 'http://' + host})[0], 200)

    def test_host_tailnet_recusado_mesmo_em_painel_hosts(self):
        for caminho in ('/', '/local.json'):
            self.assertEqual(pedir(self.local, caminho, {'Host': HOST_TAILNET})[0], 403)

    def test_hosts_fora_da_lista_exata_recusados(self):
        for host in ('localhost', '127.0.0.1', '[::1]:%d' % self.local.server_port,
                     'LOCALHOST:%d' % self.local.server_port, 'localhost:7421'):
            with self.subTest(host=host):
                self.assertEqual(pedir(self.local, headers={'Host': host})[0], 403)

    def test_host_ausente_duplicado_e_origin_duplicado_recusados(self):
        host = '127.0.0.1:%d' % self.local.server_port
        for cabecalhos in ([], [('Host', host), ('Host', HOST_TAILNET)],
                           [('Host', host), ('Origin', 'http://' + host), ('Origin', 'http://' + host)]):
            conn = http.client.HTTPConnection('127.0.0.1', self.local.server_port, timeout=3)
            try:
                conn.putrequest('GET', '/local.json', skip_host=True)
                for nome, valor in cabecalhos:
                    conn.putheader(nome, valor)
                conn.endheaders()
                resposta = conn.getresponse()
                self.assertEqual(resposta.status, 403)
                self.assertEqual(resposta.read(), b'')
            finally:
                conn.close()

    def test_parada_fecha_tambem_conexao_incompleta_ativa(self):
        iniciado = threading.Event()
        setup = servidor.HandlerLocal.setup

        def configurar(handler):
            setup(handler)
            iniciado.set()

        with patch.object(servidor.HandlerLocal, 'setup', configurar), \
                socket.create_connection(('127.0.0.1', self.local.server_port), timeout=3) as conn:
            conn.sendall(b'GET / HTTP/1.1\r\n')
            self.assertTrue(iniciado.wait(2))
            terminar = threading.Event()

            def fragmentar():
                while not terminar.wait(0.05):
                    try:
                        conn.sendall(b'x')
                    except OSError:
                        return

            fio = threading.Thread(target=fragmentar)
            fio.start()
            try:
                self.principal.parar.set()
                self.local.fio.join(1)
                self.assertFalse(self.local.fio.is_alive())
            finally:
                terminar.set()
                fio.join(2)
            self.assertFalse(fio.is_alive())
            try:
                restante = conn.recv(1)
            except (ConnectionAbortedError, ConnectionResetError):
                pass  # Windows pode sinalizar a interrupção do socket com reset em vez de EOF.
            else:
                self.assertEqual(restante, b'')

    def test_cabecalhos_proxy_recusados_inclusive_vazios(self):
        for nome in ('X-Forwarded-For', 'X-Forwarded-Host', 'X-Forwarded-Proto', 'Forwarded',
                     'Tailscale-User-Login', 'tailscale-qualquer', 'TAILSCALE-', 'X-Forwarded-',
                     'x-forwarded-qualquer', 'Via', 'vIa', 'X-Real-IP'):
            for valor in ('', 'sintetico'):
                for caminho in ('/', '/local.json'):
                    with self.subTest(nome=nome, valor=valor, caminho=caminho):
                        self.assertEqual(pedir(self.local, caminho, {nome: valor})[0], 403)

    def test_origin_estranho_recusado(self):
        for origem in ('http://exemplo.invalid', 'null', '',
                       'https://127.0.0.1:%d' % self.local.server_port):
            for caminho in ('/', '/local.json'):
                self.assertEqual(pedir(self.local, caminho, {'Origin': origem})[0], 403)

    def test_json_recusa_cross_site_e_same_site(self):
        for site in ('cross-site', 'same-site', ''):
            self.assertEqual(pedir(self.local, '/local.json', {'Sec-Fetch-Site': site})[0], 403)
        for site in ('none', 'same-origin'):
            self.assertEqual(pedir(self.local, '/local.json', {'Sec-Fetch-Site': site})[0], 200)

    def test_json_referer_tem_as_mesmas_regras_do_principal(self):
        headers = {'Referer': 'http://exemplo.invalid/'}
        self.assertEqual(pedir(self.local, '/local.json', headers)[0], 403)
        headers['Sec-Fetch-Site'] = 'same-origin'
        self.assertEqual(pedir(self.local, '/local.json', headers)[0], 200)

    def test_sem_arquivo_503_constante(self):
        self.arquivo.unlink()
        status, _, corpo = pedir(self.local, '/local.json')
        self.assertEqual((status, corpo), (503, SEM_LEITURA))

    def test_acima_de_quatro_mib_503(self):
        self.arquivo.write_bytes(b'{' + b' ' * servidor.MAX_LOCAL)
        self.assertEqual(pedir(self.local, '/local.json')[::2], (503, SEM_LEITURA))

    def test_exatamente_quatro_mib_valido(self):
        self.arquivo.write_bytes(b'{}' + b' ' * (servidor.MAX_LOCAL - 2))
        self.assertEqual(pedir(self.local, '/local.json')[::2], (200, b'{}'))

    def test_arquivo_invalido_nao_vaza_conteudo(self):
        for bruto in (b'[]', b'null', b'"SEGREDO_SINTETICO"', b'{erro', b'{"x":NaN}',
                      b'{"x":"\xff"}', b'[' * 2000):
            self.arquivo.write_bytes(bruto)
            self.assertEqual(pedir(self.local, '/local.json')[::2], (503, SEM_LEITURA))

    def test_rotas_fechadas_sem_publico_traversal_ou_listagem(self):
        for caminho in ('/dados.json', '/hermes.js', '/../', '/publico/index.html',
                        '/index.html', '/publico_local/index.html', '/%2e%2e/',
                        '/local.json.', '/local.json%20', '/LOCAL.JSON', '/%00', '/..%5c'):
            with self.subTest(caminho=caminho):
                status, _, corpo = pedir(self.local, caminho)
                self.assertEqual(status, 404)
                self.assertNotIn(b'SEGREDO_SINTETICO', corpo)

    def test_post_e_demais_metodos_405(self):
        for metodo in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT', 'OUTRO'):
            for caminho in ('/', '/local.json', '/refresh'):
                status, headers, corpo = pedir(self.local, caminho, metodo=metodo)
                self.assertEqual(status, 405)
                self.assertEqual(headers['Allow'], 'GET, HEAD')
                self.assertEqual(corpo, b'')

    def test_head_sem_corpo_preserva_status_e_tamanho(self):
        for caminho in ('/', '/local.json', '/dados.json'):
            get, gh, corpo = pedir(self.local, caminho)
            head, hh, vazio = pedir(self.local, caminho, metodo='HEAD')
            self.assertEqual(head, get)
            self.assertEqual(hh['Content-Length'], gh['Content-Length'])
            self.assertEqual(vazio, b'')
        self.arquivo.unlink()
        self.assertEqual(pedir(self.local, '/local.json', metodo='HEAD')[0], 503)

    def test_cabecalhos_de_seguranca_em_sucesso_e_erros(self):
        for caminho, headers in (('/', {}), ('/local.json', {}), ('/x', {}),
                                 ('/', {'Host': HOST_TAILNET})):
            _, recebidos, _ = pedir(self.local, caminho, headers)
            for nome, valor in (('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                                ('Cross-Origin-Resource-Policy', 'same-origin'),
                                ('Referrer-Policy', 'no-referrer')):
                self.assertEqual(recebidos[nome], valor)
            self.assertIn("frame-ancestors 'none'", recebidos['Content-Security-Policy'])
            self.assertNotIn('Access-Control-Allow-Origin', recebidos)

    def test_principal_nao_serve_arquivos_locais_nem_muda_dados(self):
        for caminho in ('/local.json', '/publico_local/index.html', '/local/', '/local/index.html'):
            self.assertEqual(pedir(self.principal, caminho)[0], 404)
        self.assertEqual(json.loads(pedir(self.principal, '/dados.json')[2]), {'origem': 'teste'})
        self.assertEqual(pedir(self.principal)[2], b'principal')

    def test_conteudo_caminhos_e_cabecalhos_nao_entram_no_log(self):
        logger = logging.getLogger('painel.servidor')
        erros = io.StringIO()
        with patch.object(logger, 'handle') as log, redirect_stderr(erros):
            pedir(self.local, '/local.json')
            pedir(self.local, '/SEGREDO_SINTETICO', {'Tailscale-User-Login': 'SEGREDO_SINTETICO'})
            log.assert_not_called()
        self.assertEqual(erros.getvalue(), '')


class ConfiguracaoLocal(unittest.TestCase):
    def test_portas_validas(self):
        for porta in (1024, 7422, 65535):
            with patch.dict(os.environ, PAINEL_PORTA_LOCAL=str(porta)):
                self.assertEqual(servidor.ler_porta_local(7421), porta)

    def test_portas_invalidas_caem_no_padrao_sem_logar_entrada(self):
        for valor in ('', '0', '1023', '65536', '-1', '1.5', ' 7423',
                      'SEGREDO_SINTETICO', '9' * 5000):
            with self.subTest(valor=valor[:20]), patch.dict(os.environ, PAINEL_PORTA_LOCAL=valor), \
                    self.assertLogs('painel.servidor', level='WARNING') as logs:
                self.assertEqual(servidor.ler_porta_local(7421), 7422)
            self.assertEqual(logs.output, [
                'WARNING:painel.servidor:porta local inválida; usando padrão 7422'])

    def test_colisao_inclusive_padrao_e_fallback_nao_cria_listener(self):
        for porta, valor in ((7422, None), (7422, '7422'), (7422, 'invalida'), (7421, '7421')):
            principal = SimpleNamespace(server_port=porta, parar=threading.Event())
            with self.subTest(porta=porta, valor=valor), patch.dict(os.environ, PAINEL_COMANDO='1'), \
                    patch.object(servidor, 'PainelLocalHTTP') as criar:
                if valor is None:
                    os.environ.pop('PAINEL_PORTA_LOCAL', None)
                else:
                    os.environ['PAINEL_PORTA_LOCAL'] = valor
                with self.assertLogs('painel.servidor', level='WARNING') as logs:
                    self.assertIsNone(servidor.iniciar_servidor_local(principal))
                criar.assert_not_called()
                esperado = ['WARNING:painel.servidor:listener local indisponível: porta coincide com a principal']
                if valor == 'invalida':
                    esperado.insert(0, 'WARNING:painel.servidor:porta local inválida; usando padrão 7422')
                self.assertEqual(logs.output, esperado)

    def test_porta_padrao_sem_variavel_e_diretorio_compartilhado(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=Path('perfil')):
            self.assertEqual(servidor.ler_porta_local(7421), 7422)
            self.assertEqual(servidor.diretorio({}), Path('perfil') / 'painel-ias')
        with patch.dict(os.environ, {'LOCALAPPDATA': 'dados_perfil'}, clear=True):
            self.assertEqual(servidor.diretorio({}), Path('dados_perfil') / 'painel-ias')

    def executar_main(self, pasta, ativado):
        ambiente = {nome: '' for nome in FLAGS}
        ambiente.update(PAINEL_IAS_DIR=str(pasta), PAINEL_HOSTS='', PAINEL_PORTA_LOCAL='7422')
        if ativado:
            ambiente['PAINEL_COMANDO'] = '1'
        locais = []
        construtor = servidor.PainelLocalHTTP

        def criar(porta, parar):
            local = construtor(0, parar)
            locais.append(local)
            return local

        saida = io.StringIO()
        with patch.dict(os.environ, ambiente), redirect_stdout(saida), \
                patch.object(servidor.signal, 'signal'), \
                patch.object(servidor, 'PainelLocalHTTP', side_effect=criar) as fabrica, \
                patch.object(servidor.PainelHTTP, 'serve_forever', side_effect=KeyboardInterrupt):
            self.assertEqual(servidor.main(['--porta', '0']), 0)
        for local in locais:
            self.assertTrue(local.parar.is_set())
            self.assertFalse(local.fio.is_alive())
            self.assertEqual(local.socket.fileno(), -1)
        return saida.getvalue(), (pasta / 'servidor.log').read_text(encoding='utf-8'), fabrica.call_count

    def test_main_desligado_stdout_e_log_originais_sem_listener(self):
        with tempfile.TemporaryDirectory() as pasta:
            saida, log, chamadas = self.executar_main(Path(pasta), False)
            self.assertRegex(saida, r'^Overview-Painel: http://127\.0\.0\.1:\d+/\n$')
            self.assertEqual(chamadas, 0)
            self.assertEqual(len(log.splitlines()), 1)
            self.assertRegex(log, r'INFO subida porta=\d+ com_coletor=False sem_gh=False sem_contas=False\n$')

    def test_main_ativado_encerra_listener_junto_do_principal(self):
        with tempfile.TemporaryDirectory() as pasta:
            _, log, chamadas = self.executar_main(Path(pasta), True)
            self.assertEqual(chamadas, 1)
            self.assertIn('listener local iniciado porta=', log)
            self.assertEqual(len(log.splitlines()), 2)

    def test_main_limita_join_e_registra_timeout_constante(self):
        local = SimpleNamespace(fio=Mock())
        local.fio.is_alive.return_value = True
        with tempfile.TemporaryDirectory() as pasta, \
                patch.dict(os.environ, PAINEL_IAS_DIR=pasta, PAINEL_HOSTS=''), \
                patch.object(servidor.signal, 'signal'), redirect_stdout(io.StringIO()), \
                patch.object(servidor, 'iniciar_servidor_local', return_value=local), \
                patch.object(servidor.PainelHTTP, 'serve_forever', side_effect=KeyboardInterrupt):
            self.assertEqual(servidor.main(['--porta', '0']), 0)
            local.fio.join.assert_called_once_with(timeout=5)
            log = (Path(pasta) / 'servidor.log').read_text(encoding='utf-8')
            self.assertIn('WARNING listener local: tempo de encerramento excedido\n', log)
            self.assertEqual(len(log.splitlines()), 2)


if __name__ == '__main__':
    unittest.main()
