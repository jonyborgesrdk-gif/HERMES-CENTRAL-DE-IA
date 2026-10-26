"""Hook best effort: sem stdout, falhas não impedem o Claude."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime


def main(argv=None, stdin=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        entrada = json.load(sys.stdin if stdin is None else stdin)
        pasta = Path(os.environ.get('PAINEL_IAS_DIR') or
                     Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'painel-ias')
        evento = {'hora': datetime.now().astimezone().isoformat(),
                  'ferramenta': 'claude', 'evento': args[0] if args else entrada['hook_event_name']}
        for chave in ('session_id', 'cwd', 'transcript_path', 'tool_name', 'message'):
            if chave in entrada:
                evento[chave] = entrada[chave]
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / 'eventos.jsonl'
        if arquivo.exists() and arquivo.stat().st_size > 5 * 1024 * 1024:
            os.replace(arquivo, pasta / 'eventos.jsonl.1')
        linha = (json.dumps(evento, ensure_ascii=False) + '\n').encode('utf-8')
        fd = os.open(arquivo, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, linha)
        finally:
            os.close(fd)
    except BaseException:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
