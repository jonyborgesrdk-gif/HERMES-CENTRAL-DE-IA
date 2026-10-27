"""Impede subprocessos de produção sem proteção contra consoles novos."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


RAIZ = Path(__file__).resolve().parent.parent
CRIADORES = {'run', 'Popen', 'call', 'check_output', 'check_call'}


def chamadas_desprotegidas(texto):
    arvore = ast.parse(texto)
    modulos = {'subprocess'}
    diretos = set()
    modulos_os = {'os'}
    proibidos = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for alias in no.names:
                if alias.name == 'subprocess':
                    modulos.add(alias.asname or alias.name)
                elif alias.name == 'os':
                    modulos_os.add(alias.asname or alias.name)
        elif isinstance(no, ast.ImportFrom):
            for alias in no.names:
                if no.module == 'subprocess' and alias.name in CRIADORES:
                    diretos.add(alias.asname or alias.name)
                elif no.module == 'os' and alias.name in {'system', 'popen'}:
                    proibidos.add(alias.asname or alias.name)
    falhas = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        func = no.func
        criador = isinstance(func, ast.Name) and func.id in diretos
        proibido = isinstance(func, ast.Name) and func.id in proibidos
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            criador |= func.value.id in modulos and func.attr in CRIADORES
            # Supervisor usa um Popen injetável; também precisa da proteção.
            criador |= func.value.id == 'self' and func.attr == 'popen'
            proibido |= func.value.id in modulos_os and func.attr in {'system', 'popen'}
        if proibido or (criador and not any(kw.arg in ('creationflags', None) for kw in no.keywords)):
            falhas.append(no.lineno)
    return sorted(falhas)


class ProtecaoEstatica(unittest.TestCase):
    def test_producao_sem_subprocessos_desprotegidos(self):
        arquivos = {RAIZ / nome for nome in ('servidor.py', 'sentinela.py', 'coletor', 'refresh-completo')}
        for pasta in ('lib', 'cotas', 'hooks'):
            arquivos.update((RAIZ / pasta).rglob('*.py'))
        falhas = []
        for arquivo in sorted(arquivos):
            for linha in chamadas_desprotegidas(arquivo.read_text(encoding='utf-8-sig')):
                falhas.append(f'{arquivo.relative_to(RAIZ).as_posix()}:{linha}')
        self.assertEqual(falhas, [], '\n'.join(falhas))

    def test_detecta_apis_aliases_e_popen_injetavel(self):
        for nome in sorted(CRIADORES):
            with self.subTest(api=nome):
                self.assertEqual(chamadas_desprotegidas(f'import subprocess as sp\nsp.{nome}([])'), [2])
                self.assertEqual(chamadas_desprotegidas(f'from subprocess import {nome} as criar\ncriar([])'), [2])
        self.assertEqual(chamadas_desprotegidas('self.popen([])'), [1])

    def test_aceita_flags_e_kwargs(self):
        for chamada in ('subprocess.run([], **sem_janela_kwargs())',
                        'subprocess.Popen([], **_novo_grupo_kwargs())',
                        'self.popen([], creationflags=flags)'):
            with self.subTest(chamada=chamada):
                self.assertEqual(chamadas_desprotegidas(chamada), [])

    def test_rejeita_atalhos_os_sem_creationflags(self):
        self.assertEqual(chamadas_desprotegidas('import os as so\nso.system("cmd")\nso.popen("cmd")'), [2, 3])
        self.assertEqual(chamadas_desprotegidas('from os import system as criar\ncriar("cmd")'), [2])


def carregar_modulo(caminho):
    spec = importlib.util.spec_from_file_location('_teste_semjanela_' + caminho.stem, caminho)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class HelpersSemJanela(unittest.TestCase):
    def test_helpers_windows_e_posix(self):
        for relativo in ('lib/semjanela.py', 'cotas/semjanela_cotas.py'):
            with self.subTest(helper=relativo):
                caminho = RAIZ / relativo
                self.assertTrue(caminho.is_file(), f'helper ausente: {relativo}')
                modulo = carregar_modulo(caminho)
                with patch.object(modulo, 'os', SimpleNamespace(name='nt')), \
                     patch.object(modulo, 'subprocess', SimpleNamespace(CREATE_NO_WINDOW=0x08000000)):
                    self.assertEqual(modulo.sem_janela_kwargs(), {'creationflags': 0x08000000})
                    primeiro = modulo.sem_janela_kwargs()
                    primeiro['creationflags'] = 0
                    self.assertEqual(modulo.sem_janela_kwargs(), {'creationflags': 0x08000000})
                # Não basta devolver zero: fora do Windows não se deve enviar o kwarg.
                with patch.object(modulo, 'os', SimpleNamespace(name='posix')), \
                     patch.object(modulo, 'subprocess', SimpleNamespace()):
                    self.assertEqual(modulo.sem_janela_kwargs(), {})

    def test_novo_grupo_preserva_flag_e_sessao_posix(self):
        with patch.object(sys, 'path', [str(RAIZ / 'cotas'), *sys.path]):
            backend = carregar_modulo(RAIZ / 'cotas/backend.py')
        with patch.object(backend.os, 'name', 'nt'), \
             patch.object(backend.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch.object(backend.subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200, create=True):
            self.assertEqual(backend._novo_grupo_kwargs(), {'creationflags': 0x08000200})
        with patch.object(backend.os, 'name', 'posix'):
            self.assertEqual(backend._novo_grupo_kwargs(), {'start_new_session': True})


if __name__ == '__main__':
    unittest.main()
