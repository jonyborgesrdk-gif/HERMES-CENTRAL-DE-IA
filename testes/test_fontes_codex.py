import json
import os
from pathlib import Path
import tempfile
import unittest
from lib.fontes import sessoes_codex


def mensagens():
    return [
        {'type': 'session_meta', 'payload': {'id': 'sintetica', 'cwd': 'C:/exemplo'}},
        {'type': 'turn_context', 'payload': {'model': 'modelo-teste'}},
        {'type': 'event_msg', 'timestamp': 'hora', 'payload': {'type': 'token_count',
         'rate_limits': {'primary': {'used_percent': 23, 'window_minutes': 300, 'resets_at': 100},
                         'secondary': {'used_percent': 45, 'window_minutes': 10080, 'resets_at': 200}}}}]


class CodexTests(unittest.TestCase):
    def test_metadados(self):
        r = sessoes_codex.interpretar(mensagens())
        self.assertEqual((r['session_id'], r['pasta'], r['modelo']),
                         ('sintetica', 'C:/exemplo', 'modelo-teste'))

    def test_usado_nao_restante(self):
        r = sessoes_codex.interpretar(mensagens())
        self.assertEqual((r['sessao_5h'], r['semanal']), (23, 45))
        self.assertEqual((r['reset_5h'], r['reset_semanal']), (100, 200))

    def test_ultimo_token(self):
        linhas = mensagens()
        ultimo = json.loads(json.dumps(linhas[-1]))
        ultimo['payload']['rate_limits']['primary']['used_percent'] = 30
        self.assertEqual(sessoes_codex.interpretar(linhas + [ultimo])['sessao_5h'], 30)

    def test_sem_cota_nao_estima(self):
        self.assertIsNone(sessoes_codex.interpretar(mensagens()[:1])['sessao_5h'])

    def ler_arquivo(self, idade):
        with tempfile.TemporaryDirectory() as pasta:
            p = Path(pasta) / 'sessao.jsonl'
            p.write_text('\n'.join(json.dumps(x) for x in mensagens()) + '\nlixo', encoding='utf-8')
            os.utime(p, (100000 - idade, 100000 - idade))
            return sessoes_codex.ler({'dir': pasta, 'agora': 100000,
                                     'listar_arquivos': lambda raiz: [p]})

    def test_recente_trabalhando(self):
        self.assertEqual(self.ler_arquivo(19)['sessoes'][0]['estado'], 'trabalhando')

    def test_vinte_segundos_ocioso(self):
        self.assertEqual(self.ler_arquivo(20)['sessoes'][0]['estado'], 'ocioso')

    def test_expirado(self):
        self.assertEqual(self.ler_arquivo(86400)['sessoes'], [])
