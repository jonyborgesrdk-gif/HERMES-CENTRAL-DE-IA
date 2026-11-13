"""Servidor local do painel: estáticos, JSON e SSE; supervisor opcional do coletor."""
import argparse
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit

from lib.semjanela import sem_janela_kwargs

sys.dont_write_bytecode = True
RAIZ = Path(__file__).resolve().parent
PORTA_COLETOR = 7420
PREFIXO = 'window.__painelDados && window.__painelDados('
# Exposição opcional via `tailscale serve`; o socket continua preso a 127.0.0.1.
_HOST_DNS = re.compile(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')


def ler_hosts_extra(valor=None):
    bruto = os.environ.get('PAINEL_HOSTS', '') if valor is None else valor
    return frozenset(item for item in (parte.strip().rstrip('.').casefold() for parte in bruto.split(','))
                     if _HOST_DNS.fullmatch(item) and not item.rsplit('.', 1)[-1].isdigit())


def ler_dados(publico):
    texto = (Path(publico) / 'dados.js').read_text(encoding='utf-8').strip()
    if not texto.startswith(PREFIXO) or not texto.endswith(');'):
        raise ValueError('dados.js inválido')
    valor = json.loads(texto[len(PREFIXO):-2])
    if not isinstance(valor, dict):
        raise ValueError('dados inválidos')
    return valor


class PainelHTTP(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, publico, porta=7421, porta_coletor=PORTA_COLETOR, hosts_extra=None):
        self.publico = Path(publico).resolve()
        self.porta_coletor = porta_coletor
        self.parar = threading.Event()
        self.hosts_extra = hosts_extra if hosts_extra is not None else ler_hosts_extra()
        super().__init__(('127.0.0.1', porta), Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'painel'
    sys_version = ''
    timeout = 60

    def version_string(self):
        return self.server_version

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            pass  # cliente encerrou uma conexão keep-alive ou SSE

    def log_message(self, *args):
        pass  # Nunca registrar payload, query, cabeçalhos ou conteúdo dos dados.

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        # Só diretivas que não dependem dos scripts/estilos inline da página: anti-clickjacking e base/form.
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        super().end_headers()

    def permitido(self):
        host = self.headers.get('Host')
        porta = self.server.server_port
        hosts = {'127.0.0.1:%d' % porta, 'localhost:%d' % porta}
        origin = self.headers.get('Origin')
        extra = self.server.hosts_extra
        if host in hosts:
            valido = origin is None or origin == 'http://' + host
        else:
            nome, _, sufixo = (host or '').rpartition(':')
            nome = nome.casefold() if sufixo == '443' else (host or '').casefold()
            valido = nome in extra and (origin is None or origin.casefold() == 'https://' + nome)
        if not valido:
            self.negar()
            return False
        return True

    def negar(self):
        self.send_response(403)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def permitido_dados(self):
        site = self.headers.get('Sec-Fetch-Site')
        if (site is not None and site not in ('same-origin', 'none')) or (
                site is None and self.headers.get('Referer') is not None):
            self.negar()
            return False
        return True

    def do_POST(self):
        """Único POST aceito: /refresh, repassado ao coletor em 127.0.0.1 (mesma origem, sem CORS)."""
        caminho = unquote(urlsplit(self.path).path)
        if not self.permitido():
            return
        if caminho != '/refresh':
            self.send_error(404)
            return
        # Navegador sempre manda Sec-Fetch-Site em fetch; fora de same-origin (ou sem o cabeçalho e
        # sem Referer/Origin estranho) é cross-site: recusa como o GET dos dados.
        site = self.headers.get('Sec-Fetch-Site')
        if site not in (None, 'same-origin') or not self.permitido_dados():
            self.negar()
            return
        try:
            tamanho = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            tamanho = -1
        if not 0 <= tamanho <= 1024:
            self.close_connection = True
            self.send_response(413)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if tamanho:
            self.rfile.read(tamanho)   # corpo ignorado, mas consumido para manter a conexão limpa
        try:
            conn = http.client.HTTPConnection('127.0.0.1', self.server.porta_coletor, timeout=2)
            try:
                conn.request('POST', '/refresh', body=b'', headers={'Content-Length': '0'})
                status = conn.getresponse().status
            finally:
                conn.close()
        except (OSError, http.client.HTTPException):
            status = None   # coletor fora do ar (conexão recusada, timeout, resposta inválida)
        if status is not None and 200 <= status < 300:
            self.send_response(204)
            self.end_headers()
            return
        codigo = 503 if status is None else 502
        self.send_response(codigo)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_HEAD(self):
        self.atender(cabeca=True)

    def do_GET(self):
        self.atender(cabeca=False)

    def atender(self, cabeca):
        if not self.permitido():
            return
        caminho = unquote(urlsplit(self.path).path)
        if '\\' in caminho or '\x00' in caminho or '..' in caminho.split('/'):
            self.send_error(403)
            return
        caminho = '/' + '/'.join(p for p in caminho.split('/') if p not in ('', '.'))
        if caminho == '/classico':
            caminho = '/classico.html'   # painel anterior; a página principal (/) é o index.html
        if caminho.casefold() in ('/dados.js', '/dados.json', '/eventos') and not self.permitido_dados():
            return
        if caminho == '/favicon.ico' and not (self.server.publico / 'favicon.ico').is_file():
            self.send_response(204)   # sem ícone: resposta vazia em vez de 404 no console
            self.end_headers()
            return
        if caminho == '/eventos':
            if cabeca:
                self.send_error(405)
            else:
                self.eventos()
            return
        try:
            if caminho == '/dados.json':
                corpo = json.dumps(ler_dados(self.server.publico), ensure_ascii=True).encode('ascii')
                tipo = 'application/json; charset=utf-8'
            else:
                arquivo = (self.server.publico / (caminho.lstrip('/') or 'index.html')).resolve()
                if not arquivo.is_relative_to(self.server.publico) or not arquivo.is_file():
                    self.send_error(404)
                    return
                if arquivo.name.casefold() in ('dados.js', 'dados.json') and not self.permitido_dados():
                    return
                corpo = arquivo.read_bytes()
                tipo = mimetypes.guess_type(arquivo.name)[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', tipo)
            self.send_header('Content-Length', str(len(corpo)))
            self.end_headers()
            if not cabeca:
                self.wfile.write(corpo)
        except (ConnectionError, TimeoutError):
            raise
        except (OSError, ValueError):
            self.send_error(503)

    def eventos(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        ultimo, resumo_ultimo, ping = None, None, time.monotonic()
        arquivo = self.server.publico / 'dados.js'
        try:
            self.wfile.write(b'retry: 2000\n\n')
            self.wfile.flush()
            while not self.server.parar.is_set():
                try:
                    stat = arquivo.stat()
                    versao = (stat.st_mtime_ns, stat.st_size)
                    # mtime tem granularidade grosseira no NTFS: duas publicações no mesmo tick e com o
                    # mesmo tamanho passam despercebidas. Nos 2 s após a escrita (arquivo pequeno)
                    # confere também o hash do conteúdo; fora disso basta o stat.
                    recente = abs(time.time_ns() - stat.st_mtime_ns) < 2_000_000_000
                    resumo = None
                    if (versao != ultimo or recente) and stat.st_size <= 4_000_000:
                        resumo = hashlib.blake2b(arquivo.read_bytes(), digest_size=16).digest()
                    if versao != ultimo or (resumo is not None and resumo != resumo_ultimo):
                        dados = json.dumps(ler_dados(self.server.publico), ensure_ascii=True)
                        self.wfile.write(('event: dados\ndata: ' + dados + '\n\n').encode('ascii'))
                        self.wfile.flush()
                        ultimo, resumo_ultimo, ping = versao, resumo, time.monotonic()
                except (FileNotFoundError, PermissionError, ValueError):
                    pass  # arquivo ainda não publicado ou em troca; manter conexão
                if time.monotonic() - ping >= 15:
                    self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
                    ping = time.monotonic()
                self.server.parar.wait(0.25)
        except OSError:
            pass  # cliente saiu ou socket ficou bloqueado


class Trava:
    def __init__(self, caminho):
        self.arquivo = open(caminho, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.arquivo.seek(0, 2)
                if not self.arquivo.tell():
                    self.arquivo.write(b'\0')
                    self.arquivo.flush()
                self.arquivo.seek(0)
                msvcrt.locking(self.arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as erro:
            self.arquivo.close()
            raise RuntimeError('já existe um servidor do painel em execução (trava ocupada)') from erro

    def close(self):
        if self.arquivo.closed:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                self.arquivo.seek(0)
                msvcrt.locking(self.arquivo.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.arquivo, fcntl.LOCK_UN)
        finally:
            self.arquivo.close()  # não remover o inode compartilhado


class JobWindows:
    """KILL_ON_JOB_CLOSE: encerra o filho mesmo em Stop-Process do servidor."""
    def __init__(self):
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(nome, ctypes.c_uint64) for nome in ('ReadOperationCount', 'WriteOperationCount',
                       'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            erro = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise erro

    def associar(self, processo):
        if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(processo._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class Supervisor:
    def __init__(self, extras=(), popen=None, job_factory=None, filho='coletor'):
        self.parar = threading.Event()
        self.processo = None
        self.popen = popen or subprocess.Popen
        self.comando = [sys.executable, '-B', str(RAIZ / filho),
                        *(['--vivo'] if filho == 'coletor' else []), *extras]
        self.job = (job_factory() if job_factory else JobWindows()) if os.name == 'nt' else None
        self.fio = threading.Thread(target=self.rodar, daemon=True)

    def rodar(self):
        while not self.parar.is_set():
            try:
                self.processo = self.popen(self.comando, cwd=RAIZ, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **sem_janela_kwargs())
                if self.job:
                    self.job.associar(self.processo)
                while not self.parar.wait(0.2):
                    if self.processo.poll() is not None:
                        break
            except OSError as erro:
                logging.getLogger('painel.servidor').warning('filho %s: falha ao iniciar/associar (%s)',
                                                             Path(self.comando[2]).name, type(erro).__name__)
                if self.processo is not None and self.processo.poll() is None:
                    self.processo.kill()
                    self.processo.wait()
            else:
                if not self.parar.is_set():
                    logging.getLogger('painel.servidor').warning(
                        'filho %s saiu (rc=%s); reiniciando em 3 s', Path(self.comando[2]).name,
                        self.processo.returncode)
            if not self.parar.is_set():
                self.parar.wait(3)

    def close(self):
        self.parar.set()
        self.fio.join(timeout=5)
        if self.processo is not None and self.processo.poll() is None:
            self.processo.terminate()
            try:
                self.processo.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.processo.kill()
                self.processo.wait()
        if self.job:
            self.job.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--porta', type=int, default=os.environ.get('PAINEL_PORTA', '7421'))
    parser.add_argument('--com-coletor', action='store_true')
    parser.add_argument('--sem-gh', action='store_true')
    parser.add_argument('--sem-contas', action='store_true')
    args = parser.parse_args(argv)
    if not 0 <= args.porta <= 65535:
        print('servidor: porta deve estar entre 0 e 65535', file=sys.stderr)
        return 2
    pasta = Path(os.environ.get('PAINEL_IAS_DIR') or
                 Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'painel-ias')
    servidor = supervisor = sentinela = trava = handler = None
    logger = logging.getLogger('painel.servidor')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    def encerrar(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, encerrar)
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        trava = Trava(pasta / 'servidor.trava')
        handler = RotatingFileHandler(pasta / 'servidor.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        servidor = PainelHTTP(RAIZ / 'publico', args.porta)
        extras = [flag for flag in ('--sem-gh', '--sem-contas') if getattr(args, flag[2:].replace('-', '_'))]
        if args.com_coletor:
            supervisor = Supervisor(extras)
            supervisor.fio.start()
            if os.environ.get('SENTINELA', '0') == '1':
                sentinela = Supervisor(filho='sentinela.py')
                sentinela.fio.start()
        logger.info('subida porta=%d com_coletor=%s sem_gh=%s sem_contas=%s',
                    servidor.server_port, args.com_coletor, args.sem_gh, args.sem_contas)
        if servidor.hosts_extra:
            logger.info('hosts extras liberados: %d (%s)', len(servidor.hosts_extra),
                        ', '.join(sorted(servidor.hosts_extra)))
        print('Overview-Painel: http://127.0.0.1:%d/' % servidor.server_port, flush=True)
        servidor.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as erro:
        print('servidor: %s' % erro, file=sys.stderr)
        return 1
    finally:
        if servidor:
            servidor.parar.set()
            servidor.server_close()
        if supervisor:
            supervisor.close()
        if sentinela:
            sentinela.close()
        if handler:
            logger.removeHandler(handler)
            handler.close()
        if trava:
            trava.close()


if __name__ == '__main__':
    sys.exit(main())
