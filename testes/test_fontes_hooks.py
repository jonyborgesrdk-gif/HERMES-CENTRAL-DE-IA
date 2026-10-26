import json
import os
from pathlib import Path
import tempfile
import unittest
from lib.fontes import eventos_hooks, transcripts_claude


class HooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        self.transcript = self.pasta / 't.jsonl'
        self.ctx = {'dir': self.pasta, 'agora': 100000}

    def eventos(self, *nomes, hora=99999):
        linhas = [dict(evento=n, session_id='abc', hora=hora,
                       transcript_path=str(self.transcript), tool_name='Read') for n in nomes]
        (self.pasta / 'eventos.jsonl').write_text(
            '\n'.join(json.dumps(e) for e in linhas), encoding='utf-8')

    def estado(self):
        return eventos_hooks.ler(self.ctx)['sessoes'][0]['estado']

    def crescer(self, mtime=99999):
        self.transcript.write_text('{}\n', encoding='utf-8')
        os.utime(self.transcript, (mtime, mtime))

    def test_sem_eventos(self):
        self.assertEqual(eventos_hooks.ler(self.ctx), {'sessoes': []})

    def test_inicio_ocioso(self):
        self.eventos('SessionStart')
        self.assertEqual(self.estado(), 'ocioso')

    def test_transcript_recente_trabalhando(self):
        self.eventos('SessionStart', hora=99990)
        self.crescer()
        self.assertEqual(self.estado(), 'trabalhando')

    def test_stop_sem_progresso_posterior_ocioso(self):
        self.eventos('SessionStart', 'Stop')
        self.crescer()
        self.assertEqual(self.estado(), 'ocioso')

    def test_turno_dois_apos_stop(self):
        self.eventos('Stop', hora=99990)
        self.crescer()
        self.assertEqual(self.estado(), 'trabalhando')

    def test_permissao_atendida_com_progresso(self):
        self.eventos('PermissionRequest', hora=99990)
        self.crescer()
        self.assertEqual(self.estado(), 'trabalhando')

    def test_stop_gravacao_final_um_segundo(self):
        self.eventos('Stop', hora=99998)
        self.crescer()
        self.assertEqual(self.estado(), 'ocioso')

    def test_margens_dois_e_tres_segundos(self):
        self.eventos('PermissionRequest', hora=99996)
        self.crescer(99998)
        self.assertEqual(self.estado(), 'esperando permissão')
        self.crescer(99999)
        self.assertEqual(self.estado(), 'ocioso')
        self.crescer(100000)
        self.assertEqual(self.estado(), 'trabalhando')

    def test_rotacionado_antes_do_atual(self):
        self.eventos('SessionStart')
        atual = self.pasta / 'eventos.jsonl'
        atual.rename(self.pasta / 'eventos.jsonl.1')
        self.assertEqual(self.estado(), 'ocioso')
        self.eventos('SessionEnd')
        self.assertEqual(eventos_hooks.ler(self.ctx)['sessoes'], [])

    def test_bytes_invalidos(self):
        self.eventos('SessionStart')
        with (self.pasta / 'eventos.jsonl').open('ab') as f:
            f.write(b'\n\xff\n')
        self.assertEqual(self.estado(), 'ocioso')

    def test_permissao_prevalece(self):
        self.eventos('PermissionRequest')
        self.crescer()
        r = eventos_hooks.ler(self.ctx)['sessoes'][0]
        self.assertEqual(r['estado'], 'esperando permissão')
        self.assertEqual(r['tool_name'], 'Read')

    def test_fim_remove(self):
        self.eventos('SessionStart', 'SessionEnd')
        self.assertEqual(eventos_hooks.ler(self.ctx)['sessoes'], [])

    def test_expiracao(self):
        self.eventos('SessionStart', hora=1000)
        self.assertEqual(eventos_hooks.ler(self.ctx)['sessoes'], [])
        self.crescer()
        self.assertEqual(self.estado(), 'trabalhando')

    def test_linha_corrompida(self):
        self.eventos('SessionStart')
        with (self.pasta / 'eventos.jsonl').open('a') as f:
            f.write('\nlixo\n[]')
        self.assertEqual(self.estado(), 'ocioso')

    def test_transcript_modelo_fala_mascarada(self):
        self.transcript.write_text(json.dumps({'type': 'assistant', 'cwd': 'C:/exemplo',
            'timestamp': '2026-01-01T00:00:00Z', 'message': {'model': 'modelo-teste',
            'content': [{'type': 'text', 'text': 'sk-' + 'a' * 40 + ' texto' * 60}]}}), encoding='utf-8')
        r = transcripts_claude.ler({'transcript_path': self.transcript})
        self.assertEqual(r['modelo'], 'modelo-teste')
        self.assertEqual(r['pasta'], 'C:/exemplo')
        self.assertLessEqual(len(r['fala']), 200)
        self.assertNotIn('sk-' + 'a' * 40, r['fala'])

    def test_so_final_64kb(self):
        self.transcript.write_text('x' * 70000 + '\n' + json.dumps(
            {'timestamp': 'fim', 'message': {'model': 'ultimo'}}) + '\n{', encoding='utf-8')
        self.assertEqual(transcripts_claude.ler({'transcript_path': self.transcript})['modelo'], 'ultimo')

    def test_transcript_ausente(self):
        self.assertIn('erro', transcripts_claude.ler({'transcript_path': self.transcript}))
