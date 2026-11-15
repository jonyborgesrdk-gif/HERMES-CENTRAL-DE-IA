'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const cotas = fs.readFileSync(path.join(__dirname, '../../publico/cotas.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '../../publico/classico.html'), 'utf8');
const hermes = fs.readFileSync(path.join(__dirname, '../../publico/hermes.css'), 'utf8');
function regraHermes(seletor) {
  const inicio = hermes.indexOf(seletor + '{');
  assert.ok(inicio >= 0, 'regra ausente: ' + seletor);
  return hermes.slice(inicio, hermes.indexOf('}', inicio));
}
function luminancia(hex) {
  const rgb = hex.replace('#', '').match(/../g).map((v) => parseInt(v, 16) / 255)
    .map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
}
function contraste(a, b) { const x = luminancia(a), y = luminancia(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); }
test('Hermes: barras passadas contrastam 3:1 em ambos os temas; período atual é mais forte', () => {
  for (const [seletor, fundo, atual] of [['.chart-bar', '#1d1d1c', '#ed925e'], ['[data-theme=light] .chart-bar', '#ffffff', '#ac572d']]) {
    const cor = /background:(#[\da-f]{6})\b/.exec(regraHermes(seletor))[1];
    assert.ok(contraste(cor, fundo) >= 3, seletor + ': ' + contraste(cor, fundo));
    assert.ok(contraste(atual, fundo) > contraste(cor, fundo));
  }
  assert.match(regraHermes('.chart-bar:last-child'), /background:var\(--accent\)/);
  assert.match(regraHermes('[data-theme=light] .chart-bar:last-child'), /background:var\(--accent\)/);
  assert.match(regraHermes('.chart-bar.zero'), /opacity:\.4/);
});
test('Hermes: gráfico contém rótulos e colunas sem mínimos intrínsecos que estourem séries densas', () => {
  assert.match(regraHermes('.chart'), /container-type:inline-size/);
  assert.match(regraHermes('.chart'), /padding-top:2\dpx/);
  assert.match(regraHermes('.chart-plot'), /grid-auto-columns:minmax\(0,1fr\)/);
  assert.match(regraHermes('.chart-x'), /grid-auto-columns:minmax\(0,1fr\)/);
  assert.match(regraHermes('.chart-bar'), /min-width:0/);
  assert.match(regraHermes('.chart-bar'), /padding:0/);
  assert.match(regraHermes('.chart-value'), /display:none/);
  assert.match(regraHermes('.chart-value'), /overflow:hidden/);
  assert.match(hermes, /@container\(min-width:350px\)\{\.chart-value\{display:block\}/);
});
function regra(fonte, seletor) {
  const i = fonte.indexOf(seletor + ' {');
  assert.ok(i >= 0, 'regra ausente: ' + seletor);
  return fonte.slice(i, fonte.indexOf('}', i));
}

test('B9 verso dos cards de cota não tem rolagem interna e divide a célula com a frente', () => {
  assert.doesNotMatch(regra(cotas, '.verso-corpo'), /overflow\s*:\s*(auto|scroll)/);
  assert.doesNotMatch(regra(cotas, '.card-verso'), /position:\s*absolute/);
  assert.match(cotas, /\.card-pilha > \.card, \.card-pilha > \.card-verso \{ grid-area: 1 \/ 1; \}/);
  // Sem altura mínima fixa: a célula cresce com o maior entre frente e verso.
  assert.doesNotMatch(cotas, /\.card-pilha > \.card \{[^}]*min-height/);
  assert.doesNotMatch(regra(cotas, '.card-verso'), /(^|[\s;])(max-)?height\s*:/);
  assert.doesNotMatch(regra(cotas, '.verso-corpo'), /(^|[\s;])(max-)?height\s*:/);
});

test('B5 gastos usa variáveis do tema (claro e escuro) nos controles e h3 menor que h2', () => {
  const controles = regra(index, '#gastos-cartao .gastos-seletor, #gastos-cartao .gastos-limpar');
  for (const v of ['--tinta', '--sup2', '--fio-forte']) assert.ok(controles.includes('var(' + v + ')'), v);
  const h3 = Number(/font-size:\s*(\d+)px/.exec(regra(index, '#gastos-cartao h3'))[1]);
  const h2 = Number(/font-size:\s*(\d+)px/.exec(regra(index, 'h2'))[1]);
  assert.ok(h3 < h2);
  assert.match(regra(index, '#gastos-cartao .gastos-coluna'), /white-space:\s*nowrap/);
});
