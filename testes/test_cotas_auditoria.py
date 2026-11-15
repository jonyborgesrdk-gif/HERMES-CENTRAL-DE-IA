"""Regressões isoladas da auditoria de cotas e auxiliares; sem dados reais."""
import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'lib'))
sys.path.insert(0, str(RAIZ / 'cotas'))
import backend
import comum
from semjanela import sem_janela_kwargs
from testes import test_sem_janela as vigia


class VigiaAuditoria(unittest.TestCase):
    def test_kwargs_desconhecidos_e_zero_nao_protegem(self):
        for texto in ('subprocess.run([], **kwargs)',
                      'subprocess.run([], creationflags=0)',
                      'opcoes = {}\nsubprocess.Popen([], **opcoes)',
                      'opcoes = sem_janela_kwargs()\nopcoes = {}\nsubprocess.Popen([], **opcoes)'):
            with self.subTest(texto=texto):
                self.assertTrue(vigia.chamadas_desprotegidas(texto))

    def test_aliases_por_atribuicao_e_asyncio(self):
        for texto in ('criar = subprocess.Popen\ncriar([])',
                      'import subprocess as sp\ncriar = sp.run\noutra = criar\noutra([])',
                      'import asyncio as aio\naio.create_subprocess_exec("cmd")',
                      'from asyncio import create_subprocess_shell as criar\ncriar("cmd")',
                      'criar = os.system\ncriar("cmd")'):
            with self.subTest(texto=texto):
                self.assertTrue(vigia.chamadas_desprotegidas(texto))

    def test_helper_atribuido_e_importado_com_alias(self):
        for texto in ('opcoes = sem_janela_kwargs()\nsubprocess.Popen([], **opcoes)',
                      'opcoes = _novo_grupo_kwargs()\nsubprocess.Popen([], **opcoes)',
                      'from semjanela import sem_janela_kwargs as ocultar\nsubprocess.run([], **ocultar())'):
            with self.subTest(texto=texto):
                self.assertEqual(vigia.chamadas_desprotegidas(texto), [])

    def test_varredura_inclui_scripts_e_hooks_sem_extensao(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            for nome in ('servidor.py', 'sentinela.py', 'coletor', 'refresh-completo',
                         'painel-missao', 'painel-pendencia', 'painel-erro', 'painel-rede', 'hooks/ao-parar'):
                alvo = raiz / nome
                alvo.parent.mkdir(exist_ok=True)
                alvo.write_text('#!/usr/bin/env python3\nsubprocess.run([])\n', encoding='utf-8')
            with patch.object(vigia, 'RAIZ', raiz):
                caso = vigia.ProtecaoEstatica('test_producao_sem_subprocessos_desprotegidos')
                with self.assertRaises(AssertionError) as erro:
                    caso.test_producao_sem_subprocessos_desprotegidos()
            for nome in ('painel-missao:2', 'painel-pendencia:2', 'painel-erro:2', 'painel-rede:2', 'hooks/ao-parar:2'):
                self.assertIn(nome, str(erro.exception))


class TravaAuditoria(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'trava msvcrt do Windows')
    def test_contencao_maior_que_10s_continua_esperando(self):
        # 400 recusas x 0,05 s = 20 s simulados, sem dormir de verdade.
        recusas = {'n': 400}
        chamadas = []

        def travar(descritor, modo, tamanho):
            chamadas.append(modo)
            if modo == comum.msvcrt.LK_NBLCK and recusas['n'] > 0:
                recusas['n'] -= 1
                raise OSError(errno.EDEADLK, 'ocupado')

        with tempfile.TemporaryDirectory() as pasta,              patch.object(comum.msvcrt, 'locking', side_effect=travar),              patch.object(comum.time, 'sleep') as dormir:
            comum.editar_quadro(lambda quadro: None, Path(pasta) / 'quadro.json')
        self.assertEqual(recusas['n'], 0)
        self.assertEqual(dormir.call_count, 400)
        self.assertEqual(chamadas[-1], comum.msvcrt.LK_UNLCK)


class ProcessosAuditoria(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'árvore de processos Windows')
    def test_encerrar_mata_neto_real_da_cli_falsa(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        codigo = ('import subprocess, sys, time; '
                  'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], '
                  'creationflags=subprocess.CREATE_NO_WINDOW); '
                  'print(p.pid, flush=True); time.sleep(60)')
        processo = subprocess.Popen([sys.executable, '-c', codigo], stdout=subprocess.PIPE,
                                    text=True, **backend._novo_grupo_kwargs())
        handle = None
        try:
            pid = int(processo.stdout.readline())
            handle = kernel.OpenProcess(0x100001, False, pid)
            self.assertTrue(handle)
            backend.stop_process(processo)
            self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0,
                             'neto sobreviveu à limpeza da consulta')
        finally:
            if handle:
                kernel.TerminateProcess(handle, 1)
                kernel.CloseHandle(handle)
            if processo.poll() is None:
                processo.kill()
            processo.wait(timeout=5)
            processo.stdout.close()

    def test_taskkill_falhando_ainda_encerra_filho(self):
        processo = Mock()
        processo.poll.return_value = None
        with patch.object(backend.os, 'name', 'nt'), \
             patch.object(backend.subprocess, 'run', side_effect=OSError('indisponível')) as executar:
            backend._encerrar(processo, True)
        self.assertTrue(executar.called)
        processo.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
