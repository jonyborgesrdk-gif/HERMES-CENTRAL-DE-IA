from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from hooks import painel_hook


class PainelHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.dict(os.environ, {'PAINEL_IAS_DIR': self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.arquivo = Path(self.tmp.name) / 'eventos.jsonl'

    def chamar(self, texto='{"session_id":"abc","cwd":"C:/x","transcript_path":"C:/x/t.jsonl"}'):
        out = io.StringIO()
        inicio = time.monotonic()
        with redirect_stdout(out):
            codigo = painel_hook.main(['SessionStart'], io.StringIO(texto))
        self.assertEqual(codigo, 0)
        self.assertEqual(out.getvalue(), '')
        self.assertLess(time.monotonic() - inicio, 1)

    def test_uma_linha(self):
        self.chamar()
        linhas = self.arquivo.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(linhas), 1)
        r = json.loads(linhas[0])
        self.assertEqual((r['evento'], r['session_id'], r['ferramenta']), ('SessionStart', 'abc', 'claude'))

    def test_json_invalido(self):
        self.chamar('{')
        self.assertFalse(self.arquivo.exists())

    def test_nao_grava_tool_input(self):
        self.chamar('{"session_id":"a","tool_input":{"segredo":"nao gravar"},"tool_name":"Read"}')
        self.assertNotIn('tool_input', self.arquivo.read_text())

    def test_rotacao(self):
        self.arquivo.write_bytes(b'x' * (5 * 1024 * 1024 + 1))
        self.chamar()
        self.assertTrue(self.arquivo.with_name('eventos.jsonl.1').exists())
        self.assertEqual(len(self.arquivo.read_text().splitlines()), 1)

    def test_falha_de_escrita(self):
        with patch.object(painel_hook.os, 'open', side_effect=PermissionError):
            self.chamar()
