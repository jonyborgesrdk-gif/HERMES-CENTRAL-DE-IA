"""E27: formatos de segredo, limites e falsos positivos deliberados."""
import json
import time
import unittest

from lib.mascara import OCULTO, _continuacoes_comandos, mascarar


class Mascara(unittest.TestCase):
    def test_desempenho_256_kib_hostis_abaixo_de_200_ms_cada(self):
        entradas = {'key': 'key.' * 65536, 'atribuicoes': 'a=' * 131072,
                    'headers': ('-H ' * 87382)[:262144], 'base64': 'A' * 262144}
        for nome, texto in entradas.items():
            with self.subTest(caso=nome):
                inicio = time.perf_counter()
                saida = mascarar(texto)
                ms = (time.perf_counter() - inicio) * 1000
                self.assertLess(ms, 200, 'mascaramento hostil ultrapassou 200 ms')
                if nome == 'base64':
                    self.assertEqual(saida, OCULTO)
        inicio = time.perf_counter()
        mascarar('a -u ' + '-u,' * 2665)
        self.assertLess((time.perf_counter() - inicio) * 1000, 50)
        for texto in (('mysql\n' * 1334)[:8000], ('mysql ' * 1334)[:8000]):
            inicio = time.perf_counter()
            mascarar(texto)
            self.assertLess((time.perf_counter() - inicio) * 1000, 50)
        hostil = ('mysql "' + '\\' + '\n') * 1000
        inicio = time.perf_counter()
        mascarar(hostil[:8000])
        self.assertLess((time.perf_counter() - inicio) * 1000, 50)

    def test_continuacoes_preservam_comprimento_e_indices(self):
        for ultimo in ('\\', '`', '^', '|', '&'):
            for quebra in ('\n', '\r\n'):
                entrada = 'mysql ' + ultimo + '  ' + quebra + '-psegredo'
                saida = _continuacoes_comandos(entrada)
                self.assertEqual(len(saida), len(entrada))
                self.assertEqual(saida.index('-psegredo'), entrada.index('-psegredo'))
                self.assertNotIn(';', saida)
        self.assertEqual(_continuacoes_comandos('cd /app\nmysql'), 'cd /app;mysql')

    def test_continuacao_isolada_vira_espaco_sem_apagar_caminho(self):
        for char in ('\\', '`', '^'):
            for quebra in ('\n', '\r\n'):
                entrada = 'sqlcmd -P ' + char + '  ' + quebra + 'hunter2'
                saida = _continuacoes_comandos(entrada)
                self.assertEqual(len(saida), len(entrada))
                self.assertEqual(saida[len('sqlcmd -P ')], ' ')
                self.assertEqual(saida.index('hunter2'), entrada.index('hunter2'))
                self.assertNotIn('hunter2', mascarar(entrada))
        entrada = 'net use Z: ' + '\\\\srv\\share\\' + '\nmysql -phunter2'
        saida = _continuacoes_comandos(entrada)
        self.assertEqual(saida[entrada.index('\n') - 1], '\\')

    def test_flags_sem_senha_permanecem_visiveis(self):
        for texto in ('docker run -p 8080:80', 'ssh -p 22', 'mkdir -p a/b',
                      'mysql -P3306', 'echo mysql; find -print', 'echo mysql; find -path a/b',
                      '7z x a.zip -oC:/out', 'mysql -h db -P3306 -u root',
                      'sqlcmd -S . -E', 'docker login -u u registry.io'):
            with self.subTest(entrada=texto):
                self.assertEqual(mascarar(texto), texto)

    def test_nome_mcp_sem_longo_mas_tokens_conhecidos_ocultos(self):
        nome = 'mcp__hindsight__hindsight_search_pages'
        self.assertEqual(mascarar(nome, sequencias_longas=False), nome)
        self.assertEqual(mascarar('sk-tokenFalso123', sequencias_longas=False), OCULTO)

    def test_argv_direto_do_probe_mantem_contexto(self):
        self.assertNotIn('hunter2', mascarar(['docker', 'login', '-p', 'hunter2']))

    def test_controles_bidi_e_falsos_positivos_documentados(self):
        self.assertEqual(mascarar('echo a\x1b\x00\x7fb\u202e\u2066\u200ec'), 'echo abc')
        for valor in ('-Pass\u202eword hunter2', 'Bearer\thunter2', 'TOKEN=\x1bhunter2'):
            self.assertNotIn('hunter2', mascarar(valor))
        for valor in ('12345678-1234-1234-1234-123456789abc', 'a' * 40,
                      '/usr/local/lib/python3/site-packages', 'Basic usage'):
            self.assertIn(OCULTO, mascarar(valor))

    def test_variaveis_flags_json_e_headers_por_nome(self):
        nomes = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASSWD', 'PWD', 'AUTH',
                 'COOKIE', 'SESSION', 'CREDENTIAL', 'PASS', 'PASSPHRASE', 'PIN', 'SIG', 'SIGNATURE')
        for nome in nomes:
            for chave in (nome, 'prefix_' + nome.lower() + '_suffix'):
                for forma in ('%s=SEGREDO', '%s="SEGREDO com espaço"', "%s='SEGREDO com espaço'",
                              '--%s SEGREDO', '--%s=SEGREDO', '--%s "SEGREDO com espaço"',
                              '"%s": "SEGREDO com espaço"', "'%s': 'SEGREDO com espaço'",
                              '-H "%s: SEGREDO com espaço"'):
                    with self.subTest(chave=chave, forma=forma):
                        saida = mascarar(forma % chave)
                        self.assertNotIn('SEGREDO', saida)
                        self.assertIn(OCULTO, saida)

    def test_esquemas_urls_e_pem(self):
        for texto in ('Bearer SEGREDO', 'bearer SEGREDO', 'Basic SEGREDO',
                      'curl -H "Authorization: Bearer SEGREDO" /api',
                      'curl -H "Cookie: primeiro=SEGREDO; segundo=SEGREDO"',
                      'https://usuario:SEGREDO@exemplo.invalid/a',
                      '-----BEGIN PRIVATE KEY-----\nSEGREDO\n-----END PRIVATE KEY-----',
                      '-----BEGIN RSA PRIVATE KEY-----\nSEGREDO',
                      '-----BEGIN OPENSSH PRIVATE KEY-----\nSEGREDO\n-----END OPENSSH PRIVATE KEY-----'):
            with self.subTest(texto=texto):
                self.assertNotIn('SEGREDO', mascarar(texto))
                self.assertIn(OCULTO, mascarar(texto))
        self.assertEqual(mascarar('https://u:senha@host/a'), 'https://' + OCULTO + '@host/a')

    def test_tokens_conhecidos(self):
        tokens = ('sk-' + 'aB12' * 8, 'sk-ant-' + 'aB12' * 8, 'xai-' + 'aB12' * 8,
                  'ghp_abcdef123', 'gho_abcdef123', 'ghu_abcdef123', 'ghs_abcdef123',
                  'github_pat_abcdef123', 'glpat-abcdef123', 'AKIA' + 'X' * 16,
                  'AIza' + 'aB12' * 8, 'xoxa-123456789', 'xoxb-123456789', 'xoxp-123456789',
                  'eyJhbGci.eyJzdWIi.c2lnbmF0dXJl')
        for token in tokens:
            with self.subTest(prefixo=token[:7]):
                self.assertEqual(mascarar('echo ' + token), 'echo ' + OCULTO)

    def test_hex_base64_e_hash_git_falso_positivo_documentado(self):
        for token in ('abcdef0123456789' * 2, 'abcdef0123456789' * 2 + 'abcdef01',
                      'aB+/019x' * 5 + '==', 'aB-_019x' * 5):
            self.assertEqual(mascarar('echo ' + token), 'echo ' + OCULTO)
        self.assertIn('hash Git', __import__('lib.mascara', fromlist=['']).__doc__)

    def test_texto_legitimo_e_caminhos(self):
        for texto in ('', 'git status --short', 'py -m unittest testes.test_mascara',
                      'C:\\projetos\\painel\\lib\\arquivo.py', '/home/ana/projeto/src/main.py',
                      'https://exemplo.invalid/docs', 'abc123', 'a' * 31,
                      '--monkeypatch x', 'Read(file_path="arquivo.py")'):
            # KEY é substring intencional: monkeypatch também é sensível.
            if 'monkeypatch' not in texto:
                self.assertEqual(mascarar(texto), texto)

    def test_json_aninhado_e_aspas_escapadas(self):
        bruto = json.dumps({'args': {'password': ['SEGREDO', 'SEGREDO']},
                            'input': 'curl --api-key "SEGREDO com espaço"',
                            'plain': 'C:\\projeto\\main.py'})
        saida = mascarar(bruto)
        self.assertNotIn('SEGREDO', saida)
        self.assertEqual(json.loads(saida)['plain'], 'C:\\projeto\\main.py')
        self.assertIn(OCULTO, saida)

    def test_multiplos_segredos_idempotencia_e_pureza(self):
        entrada = 'KEY=SEGREDO --auth SEGREDO Bearer SEGREDO sk-abc123456'
        saida = mascarar(entrada)
        self.assertEqual(entrada, 'KEY=SEGREDO --auth SEGREDO Bearer SEGREDO sk-abc123456')
        self.assertNotIn('SEGREDO', saida)
        self.assertEqual(mascarar(saida), saida)


# Uma entrada exata da tabela por teste (credenciais exclusivamente fictícias).
_REVISAO = (
    (r'curl -d "{\"password\":\"hunter2\"}" https://api', 'hunter2'),
    ('redis-cli -u redis://:hunter2@localhost:6379', 'hunter2'),
    ('export DB_PASS=hunter2', 'hunter2'),
    ('docker run -e SMTP_PASS=x', 'x'),
    ('GPG_PASSPHRASE=x', 'x'),
    ('gpg --passphrase x', 'x'),
    ('openssl … -passin pass:x', 'x'),
    ("ConvertTo-SecureString 'Hunter2!' -AsPlainText -Force", 'Hunter2!'),
    ("Connect-X -Password 'Hunter2!'", 'Hunter2!'),
    ('-Token abc123', 'abc123'),
    ('curl -u admin:hunter2 https://api.x', 'admin:hunter2'),
    ('curl --user admin:hunter2', 'admin:hunter2'),
    ('mysql -u root -phunter2', 'hunter2'),
    ('sshpass -p hunter2 ssh h', 'hunter2'),
    ('sqlcmd -U sa -P Hunter2', 'Hunter2'),
    ('setx DB_PASSWORD hunter2', 'hunter2'),
    ("[Environment]::SetEnvironmentVariable('API_KEY','abc123','User')", 'abc123'),
    ('os.environ["DB_PASSWORD"] = "hunter2"', 'hunter2'),
    ("cfg['api_key'] = 'abc123'", 'abc123'),
    ('set "API_KEY=abc def"', 'abc def'),
    ('PASSWORD=abc,def', 'abc,def'),
    ('…?sv=2022-11-02&sig=AbC%2Bdef%2Fghi%3D', 'AbC%2Bdef%2Fghi%3D'),
    ('curl -u admin:hunter2 https://x', 'hunter2'),
    ('Connect-X -Password hunter2', 'hunter2'),
    ('-ApiKey "abc def, ghi"', 'abc def, ghi'),
    ('-Secret hunter2', 'hunter2'),
    ('-Credential hunter2', 'hunter2'),
    ('PASSWD=hunter2 PIN=482913 SIGNATURE=abc%def', 'hunter2'),
    ('PASSWORD="abc, def"', 'abc, def'),
)


_SENHAS_E27C = (
    'docker login -p hunter2', 'docker login --password hunter2',
    'az login -u a -p hunter2', 'echo hunter2 | docker login --password-stdin',
    'redis-cli -a hunter2', '7z x -phunter2 a.7z', 'zip -P hunter2 a.zip',
    'unzip -P hunter2 a.zip', 'mariadb -phunter2', 'sshpass -phunter2 ssh h',
    'ldapsearch -w hunter2', 'ssh-keygen -N hunter2', 'openssl enc -k hunter2',
    'openssl enc -pass pass:hunter2', r'net use \\s\x hunter2 /user:u',
    'Open-MySqlConnection -Password hunter2',
    'cd /app\nmysql -uroot -phunter2',
    'docker exec -it sql1 sqlcmd -S localhost -U sa -P hunter2',
    'docker exec -it sql1 /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "hunter2"',
    'mysqldump -uroot -phunter2 db',
    'mysqladmin -uroot -phunter2',
    'sudo mysql -phunter2',
    'kubectl exec p -- mysql -phunter2',
    r'net use Z: \\s\x hunter2 /user:u',
    r'net use * \\s\x hunter2',
    r'net use \\s\x /user:u hunter2',
    r'net use Z: \\s\x /user:u hunter2',
    'bash -c "sshpass -phunter2 ssh h"',
    'bash -c "sshpass -p hunter2 ssh h"',
    'bash -lc "cd /app\nmysql -phunter2"',
    'sh -c "mysql -phunter2"',
    'cmd /c "mysql -phunter2"',
    'powershell -Command "mysql -phunter2"',
    'env mysql -phunter2',
    'time mariadb-dump -phunter2',
    'echo a; docker login -p hunter2 r',
    'curl -u "john doe:hunter2"',
    'mysql -h db ' + '\\' + '\n' + '  -uroot -phunter2',
    'sqlcmd -S . -U sa ' + '`' + '\n' + ' -P hunter2',
    'docker login r ' + '\\' + '\n' + ' -u u ' + '\\' + '\n' + ' -p hunter2',
    'echo hunter2 |' + '\n' + 'docker login -u u --password-stdin',
    'ssh db "mysql -uroot -phunter2"',
    'powershell -NoProfile -Command "sqlcmd -S . -U sa -P hunter2"',
    'pwsh -c "mysql -phunter2"',
    'bash -c "echo ' + '\\' + '"a b' + '\\' + '"; mysql -phunter2"',
    "# don't\nmysql -uroot -phunter2",
    ('bash -lc "mysql -phunter2; ' + 'x ' * 4000 + '"')[:8000],
    'sqlcmd -S . -U sa -P"hunter2"',
    'net use Z: ' + '\\\\srv\\share\\' + '\n' + 'mysql -uroot -phunter2',
    'MYSQL="mysql -h db"\n$MYSQL -uroot -phunter2',
    '$mysql = "C:\\mysql.exe"\n& $mysql -uroot -phunter2',
    '$sqlcmd -S . -U sa -P hunter2',
    'docker login -u u -p ' + '\\' + '\n' + 'hunter2',
    'sshpass -p ' + '\\' + '\n' + 'hunter2 ssh h',
    'sqlcmd -S . -U sa -P ' + '`' + '\n' + 'hunter2',
    'sqlcmd -P\'hunter2\'',
    'bash -l -c "mysql -phunter2"',
    'bash -ec "mysql -phunter2"',
    'su -c "mysql -phunter2"',
    '${MYSQL} -uroot -phunter2',
    '& $sqlcmd -P hunter2',
    # E27e: as 13 entradas exatas do probe_e27d, uma por teste gerado.
    '7z x $zip -phunter2',
    '7z x "${ZIP}" -phunter2',
    'openssl enc -aes-256-cbc -in $zip -out a.enc -k hunter2',
    'openssl enc -in $MYSQL_DUMP -out a.enc -k hunter2',
    'echo hunter2 | docker login -u $MYSQL_USER --password-stdin',
    '& $env:MYSQL -uroot -phunter2',
    '%SQLCMD% -S . -U sa -P hunter2',
    '$(which sshpass) -p hunter2 ssh h',
    'docker login -u u -phunter2',
    'az login -u u -phunter2',
    'ldapsearch -x -whunter2',
    'ssh-keygen -Nhunter2',
    'sqlcmd -S . -U sa -Phunter2',
    # Demais formas de M-1/M-2 não presentes no probe.
    '& $EnV:MYSQL -phunter2',
    '`mysql` -phunter2',
    'ldapmodify -whunter2',
    'ldapmodify -w hunter2',
    'ssh-keygen -Phunter2',
    "ssh-keygen -P'hunter2'",
    'net use Z: ' + '\\\\srv\\share\\' + '\n' + "sqlcmd -S . -U sa -P'hunter2'",
)


def _teste_entrada(entrada, segredo):
    def testar(self):
        resultado = mascarar(entrada)
        self.assertNotIn(segredo, resultado)
        for parte in segredo.replace(',', ' ').replace(':', ' ').split():
            if len(parte) >= 3:
                self.assertNotIn(parte, resultado)
        self.assertIn(OCULTO, resultado)
    return testar


for _i, (_entrada, _segredo) in enumerate(_REVISAO, 1):
    setattr(Mascara, 'test_revisor_entrada_%02d' % _i, _teste_entrada(_entrada, _segredo))
for _i, _entrada in enumerate(_SENHAS_E27C, 1):
    setattr(Mascara, 'test_e27c_senha_%02d' % _i, _teste_entrada(_entrada, 'hunter2'))


if __name__ == '__main__':
    unittest.main()
