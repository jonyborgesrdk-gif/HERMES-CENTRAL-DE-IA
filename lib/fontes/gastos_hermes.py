"""Contadores das sessões Hermes; leitura SQLite sem conteúdo das conversas."""
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import re

from . import agora

CAMPOS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
          'reasoning_tokens', 'message_count', 'cost_usd')
# Allowlist explícita: não usar SELECT *, títulos, identidades ou a tabela messages.
CONSULTA = '''SELECT source,
    COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0),
    COALESCE(SUM(cache_read_tokens), 0), COALESCE(SUM(cache_write_tokens), 0),
    COALESCE(SUM(reasoning_tokens), 0), COALESCE(SUM(message_count), 0),
    COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0)
    FROM sessions WHERE started_at >= ? AND started_at <= ? GROUP BY source ORDER BY source'''


def ler(ctx):
    caminho = Path(ctx.get('gastos_path') or
                   Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'hermes' / 'state.db')
    hora = agora(ctx)
    hoje = datetime.fromtimestamp(hora).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    resultado: dict = {'custo_tipo': 'estimado pelo Hermes'}
    try:
        with closing(sqlite3.connect(caminho.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            # As duas janelas observam o mesmo snapshot, mesmo com o Hermes escrevendo em WAL.
            db.execute('BEGIN')
            for janela, desde in (('hoje', hoje), ('7d', hora - 7 * 86400)):
                origens, total = {}, dict.fromkeys(CAMPOS, 0)
                for linha in db.execute(CONSULTA, (desde, hora)):
                    source = linha[0]
                    if not isinstance(source, str) or not re.fullmatch(r'[a-z][a-z_-]{0,39}', source):
                        source = 'desconhecida'
                    origem = origens.setdefault(source, dict.fromkeys(CAMPOS, 0))
                    for campo, valor in zip(CAMPOS, linha[1:]):
                        origem[campo] += valor
                        total[campo] += valor
                resultado[janela] = {'desde': desde,
                    'origens': [dict(origens[s], source=s) for s in sorted(origens)], 'total': total}
    except (sqlite3.Error, OSError):
        # Não publicar caminho, SQL, conteúdo nem erro cru do banco.
        return {'erro': 'banco Hermes indisponível, travado ou incompatível'}
    return {'gastos_hermes': resultado}
