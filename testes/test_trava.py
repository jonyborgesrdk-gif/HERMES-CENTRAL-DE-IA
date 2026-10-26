"""Contrato de gravação e liberação da trava do quadro."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
import comum


class TravaTests(unittest.TestCase):
    def test_editar_grava_e_le(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / 'quadro.json'
            resultado = comum.editar_quadro(
                lambda quadro: quadro['missoes'].update({'Agente': {'missao': 'teste'}}),
                caminho)
            self.assertIsNone(resultado)
            self.assertEqual(comum.carregar_quadro(caminho)['missoes']['Agente'],
                             {'missao': 'teste'})

    @unittest.skipUnless(os.name == 'nt', 'trava msvcrt do Windows')
    def test_aquisicoes_sequenciais_e_liberacao(self):
        import msvcrt
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / 'quadro.json'
            for _ in range(2):
                comum.editar_quadro(lambda quadro: None, caminho)
                with open(str(caminho) + '.trava', 'r+b') as trava:
                    msvcrt.locking(trava.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(trava.fileno(), msvcrt.LK_UNLCK, 1)
            with self.assertRaises(RuntimeError):
                comum.editar_quadro(lambda quadro: (_ for _ in ()).throw(RuntimeError()), caminho)
            comum.editar_quadro(lambda quadro: None, caminho)
