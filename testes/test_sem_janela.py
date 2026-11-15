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
    helpers = {'sem_janela_kwargs', '_novo_grupo_kwargs'}
    assincronos = {'create_subprocess_exec', 'create_subprocess_shell'}

    class Vigia(ast.NodeVisitor):
        def __init__(self):
            self.nomes = {nome: nome for nome in ('subprocess', 'asyncio', 'os')}
            self.nomes.update({nome: 'helper' for nome in helpers})
            self.falhas = set()

        def atributo(self, modulo, nome):
            if ((modulo == 'subprocess' and nome in CRIADORES)
                    or (modulo == 'asyncio' and nome in assincronos)):
                return 'criador'
            if modulo == 'os' and nome in {'system', 'popen'}:
                return 'proibido'
            return None

        def origem(self, no):
            if isinstance(no, ast.Name):
                return self.nomes.get(no.id)
            if isinstance(no, ast.Attribute):
                if isinstance(no.value, ast.Name) and no.value.id == 'self' and no.attr == 'popen':
                    return 'criador'
                return self.atributo(self.origem(no.value), no.attr)
            if isinstance(no, ast.Call) and self.origem(no.func) == 'helper':
                return 'kwargs'
            return None

        def visit_Import(self, no):
            for alias in no.names:
                self.nomes[alias.asname or alias.name] = alias.name

        def visit_ImportFrom(self, no):
            for alias in no.names:
                self.nomes[alias.asname or alias.name] = (
                    'helper' if alias.name in helpers else self.atributo(no.module, alias.name))

        def atribuir(self, alvo, origem):
            if isinstance(alvo, ast.Name):
                self.nomes[alvo.id] = origem
            elif isinstance(alvo, (ast.Tuple, ast.List)):
                for item in alvo.elts:
                    self.atribuir(item, None)
            elif isinstance(alvo, ast.Subscript):
                self.atribuir(alvo.value, None)  # dict alterado deixa de ser comprovado

        def visit_Assign(self, no):
            self.visit(no.value)
            origem = self.origem(no.value)
            for alvo in no.targets:
                self.atribuir(alvo, origem)

        def visit_AnnAssign(self, no):
            if no.value is not None:
                self.visit(no.value)
            self.atribuir(no.target, self.origem(no.value))

        def visit_FunctionDef(self, no):
            anteriores = self.nomes.copy()
            # Parâmetros não herdam a procedência de variáveis externas homônimas.
            for argumento in (*no.args.posonlyargs, *no.args.args, *no.args.kwonlyargs):
                self.nomes[argumento.arg] = None
            for argumento in (no.args.vararg, no.args.kwarg):
                if argumento:
                    self.nomes[argumento.arg] = None
            self.generic_visit(no)
            self.nomes = anteriores

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_If(self, no):
            self.visit(no.test)
            antes = self.nomes.copy()
            for item in no.body:
                self.visit(item)
            sim = self.nomes.copy()
            self.nomes = antes.copy()
            for item in no.orelse:
                self.visit(item)
            # Só confia em kwargs se a origem for a mesma nos dois caminhos.
            self.nomes = {k: v if self.nomes.get(k) == v else None for k, v in sim.items()}

        def visit_Call(self, no):
            origem = self.origem(no.func)
            protegido = any(
                (kw.arg is None and self.origem(kw.value) == 'kwargs')
                or (kw.arg == 'creationflags' and not (
                    isinstance(kw.value, ast.Constant) and not kw.value.value))
                for kw in no.keywords)
            if origem == 'proibido' or (origem == 'criador' and not protegido):
                self.falhas.add(no.lineno)
            self.generic_visit(no)
            if isinstance(no.func, ast.Attribute) and self.origem(no.func.value) == 'kwargs':
                self.atribuir(no.func.value, None)

    vigia = Vigia()
    vigia.visit(ast.parse(texto))
    return sorted(vigia.falhas)


class ProtecaoEstatica(unittest.TestCase):
    def test_producao_sem_subprocessos_desprotegidos(self):
        arquivos = {RAIZ / nome for nome in ('servidor.py', 'sentinela.py', 'coletor', 'refresh-completo',
                                            'painel-missao', 'painel-pendencia', 'painel-erro')}
        for pasta in ('lib', 'cotas', 'hooks'):
            arquivos.update((RAIZ / pasta).rglob('*.py'))
        for arquivo in (RAIZ / 'hooks').rglob('*'):
            if arquivo.is_file() and not arquivo.suffix:
                arquivos.add(arquivo)
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
