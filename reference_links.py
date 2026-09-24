"""Resolve direct TypeScript imports between changed files using TypeScript's resolver."""
import json
import os
import subprocess
from pathlib import Path


_NODE = r"""
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const root = path.resolve(process.argv[1]);
const changed = JSON.parse(process.argv[2]);
const programs = new Map();
function programFor(absolute) {
  const configPath = ts.findConfigFile(path.dirname(absolute), ts.sys.fileExists, 'tsconfig.json');
  if (!configPath) return null;
  if (programs.has(configPath)) return programs.get(configPath);
  const config = ts.readConfigFile(configPath, ts.sys.readFile);
  if (config.error) throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'));
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, path.dirname(configPath));
  const explicitSources = changed.map(file => path.join(root, file)).filter(file => /\.[cm]?tsx?$/.test(file) && fs.existsSync(file));
  const program = ts.createProgram([...new Set([...parsed.fileNames, ...explicitSources])], parsed.options);
  programs.set(configPath, program);
  return program;
}
function resolveImport(from, specifier) {
  const config = ts.findConfigFile(path.dirname(from), ts.sys.fileExists, 'tsconfig.json');
  const parsed = config && ts.readConfigFile(config, ts.sys.readFile);
  const options = config && !parsed.error
    ? ts.parseJsonConfigFileContent(parsed.config, ts.sys, path.dirname(config)).options : {};
  const result = ts.resolveModuleName(specifier, from, options, ts.sys).resolvedModule;
  if (!result) return null;
  const resolved = path.resolve(result.resolvedFileName);
  if (!resolved.startsWith(root + path.sep)) return null;
  return path.relative(root, resolved).split(path.sep).join('/');
}
const changedSet = new Set(changed);
const output = {};
for (const relative of [...changed].sort()) {
  const absolute = path.join(root, relative);
  if (!/\.[cm]?tsx?$/.test(relative) || !fs.existsSync(absolute)) continue;
  const program = programFor(absolute);
  const checker = program?.getTypeChecker();
  const source = program?.getSourceFile(absolute) || ts.createSourceFile(absolute, fs.readFileSync(absolute, 'utf8'), ts.ScriptTarget.Latest, true);
  const links = [];
  function add(node, specifier, symbols, relation) {
    const target = resolveImport(absolute, specifier);
    if (!target || !changedSet.has(target)) return;
    const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
    if (!symbols.length) links.push({to: target, kind: relation, line});
    else for (const entry of symbols) links.push({to: target, kind: entry.typeOnly ? 'imports type from' : relation,
      symbol: entry.name, line});
  }
  function addResolved(node, symbol, relation) {
    if (!checker || !symbol) return;
    if (symbol.flags & ts.SymbolFlags.Alias) {
      try { symbol = checker.getAliasedSymbol(symbol); } catch { return; }
    }
    for (const declaration of symbol.getDeclarations() || []) {
      const target = path.resolve(declaration.getSourceFile().fileName);
      if (!target.startsWith(root + path.sep)) continue;
      const relativeTarget = path.relative(root, target).split(path.sep).join('/');
      if (!changedSet.has(relativeTarget) || relativeTarget === relative) continue;
      const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
      links.push({to: relativeTarget, kind: relation, symbol: symbol.getName(), line});
    }
  }
  function visit(node) {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const clause = node.importClause;
      const symbols = [];
      if (clause?.name) symbols.push({name: clause.name.text, typeOnly: !!clause.isTypeOnly});
      const bindings = clause?.namedBindings;
      if (bindings && ts.isNamespaceImport(bindings)) symbols.push({name: '* as ' + bindings.name.text, typeOnly: !!clause.isTypeOnly});
      if (bindings && ts.isNamedImports(bindings)) {
        for (const item of bindings.elements) symbols.push({name: (item.propertyName || item.name).text,
          typeOnly: !!clause.isTypeOnly || item.isTypeOnly});
      }
      add(node, node.moduleSpecifier.text, symbols, 'imports from');
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      const symbols = node.exportClause && ts.isNamedExports(node.exportClause)
        ? node.exportClause.elements.map(item => ({name: (item.propertyName || item.name).text,
          typeOnly: node.isTypeOnly || item.isTypeOnly})) : [];
      add(node, node.moduleSpecifier.text, symbols, 're-exports from');
    } else if (ts.isCallExpression(node)) {
      if (ts.isIdentifier(node.expression) && node.expression.text === 'readFileSync' &&
          source.statements.some(statement => ts.isImportDeclaration(statement) &&
            ['node:fs', 'fs'].includes(statement.moduleSpecifier.text) &&
            statement.importClause?.namedBindings && ts.isNamedImports(statement.importClause.namedBindings) &&
            statement.importClause.namedBindings.elements.some(item => item.name.text === 'readFileSync'))) {
        const first = node.arguments[0];
        if (first && ts.isNewExpression(first) && ts.isIdentifier(first.expression) &&
            first.expression.text === 'URL' && first.arguments?.length >= 1 && ts.isStringLiteral(first.arguments[0])) {
          const target = path.resolve(path.dirname(absolute), first.arguments[0].text);
          const relativeTarget = path.relative(root, target).split(path.sep).join('/');
          if (target.startsWith(root + path.sep) && changedSet.has(relativeTarget)) {
            links.push({to: relativeTarget, kind: 'reads',
              line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1});
          }
        }
      }
      addResolved(node, checker?.getSymbolAtLocation(node.expression), 'calls');
    } else if (ts.isTypeReferenceNode(node)) {
      addResolved(node, checker?.getSymbolAtLocation(node.typeName), 'uses type');
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  const unique = new Map(links.map(link => [JSON.stringify(link), link]));
  const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  output[relative] = [...unique.values()].sort((a, b) =>
    compareText(a.to, b.to) || a.line - b.line || compareText(a.kind, b.kind) || compareText(a.symbol || '', b.symbol || ''));
}
process.stdout.write(JSON.stringify(output));
"""


def resolve_changed_imports(root, changed_paths, *, timeout=30):
    """Return deterministic direct import links; requires node and workspace TypeScript."""
    root = Path(root).resolve()
    changed = sorted({str(Path(path).as_posix()) for path in changed_paths})
    try:
        result = subprocess.run(
            ['node', '-e', _NODE, str(root), json.dumps(changed)],
            cwd=root, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f'Could not resolve TypeScript imports: {error}') from error
    if result.returncode:
        raise RuntimeError('Could not resolve TypeScript imports: ' + (result.stderr.strip() or 'node failed'))
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError('TypeScript import resolver returned invalid JSON') from error
