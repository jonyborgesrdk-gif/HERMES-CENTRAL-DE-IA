"""Hook best effort: sem stdout, falhas não impedem o Claude."""
import json
import io
import os
from pathlib import Path
import sys
import threading
import time
from datetime import datetime

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
from leitor import mascarar


def _gravar(argv, stdin, deadline):
    try:
        args = sys.argv[1:] if argv is None else argv
        entrada = json.load(stdin)
        pasta = Path(os.environ.get('PAINEL_IAS_DIR') or
                     Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'painel-ias')
        evento = {'hora': datetime.now().astimezone().isoformat(),
                  'ferramenta': 'claude', 'evento': args[0] if args else entrada['hook_event_name']}
        for chave in ('session_id', 'cwd', 'transcript_path', 'tool_name', 'message'):
            if chave in entrada:
                evento[chave] = entrada[chave]
        if 'message' in evento:
            evento['message'] = mascarar(str(evento['message']))[:200]
        if time.monotonic() >= deadline:
            return
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / 'eventos.jsonl'
        if arquivo.exists() and arquivo.stat().st_size > 5 * 1024 * 1024:
            if time.monotonic() >= deadline:
                return
            os.replace(arquivo, pasta / 'eventos.jsonl.1')
        linha = (json.dumps(evento, ensure_ascii=False) + '\n').encode('utf-8')
        if time.monotonic() >= deadline:
            return
        fd = os.open(arquivo, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if time.monotonic() < deadline:
                os.write(fd, linha)
        finally:
            os.close(fd)
    except BaseException:
        pass


def main(argv=None, stdin=None, _cli=False):
    deadline = time.monotonic() + 0.8
    try:
        entrada = stdin if stdin is not None else io.TextIOWrapper(
            sys.stdin.buffer, encoding='utf-8', errors='replace')
        if _cli:
            _gravar(argv, entrada, deadline)
        else:
            worker = threading.Thread(target=_gravar, args=(argv, entrada, deadline), daemon=True)
            worker.start()
            worker.join(max(0, deadline - time.monotonic()))
    except BaseException:
        pass
    return 0


if __name__ == '__main__':
    # O deadline evita gravações tardias; o watchdog encerra até stdin/I/O bloqueado.
    watchdog = threading.Timer(0.8, lambda: os._exit(0))
    watchdog.daemon = True
    watchdog.start()
    codigo = main(_cli=True)
    watchdog.cancel()
    sys.exit(codigo)
