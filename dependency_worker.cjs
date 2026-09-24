#!/usr/bin/env node
/* Static links only. Uses the reviewed clone's compiler, never a global compiler.
 * No tsserver plugins, builds, package installs, or repository scripts are run.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');

const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const progress = message => process.stderr.write(message + '\n');

function main(request) {
  const root = fs.realpathSync(request.root);
  const requireHere = createRequire(path.join(root, 'package.json'));
  let compiler;
  try {
    compiler = requireHere.resolve('typescript');
    // Do not silently use a compiler inherited from a parent/global install.
    if (!compiler.startsWith(root + path.sep) && !fs.existsSync(path.join(root, 'node_modules/typescript'))) {
      throw new Error('TypeScript was resolved outside the review clone');
    }
  } catch (_) {
    throw new Error('TypeScript is not installed in the review clone after dependency setup. Check that the repository declares TypeScript, then reopen the diff map.');
  }
  const ts = requireHere('typescript');
  // Some repositories wrap TypeScript. Include the implementation behind that
  // wrapper as well, not just its tiny entry file.
  const compilerFiles = Object.keys(require.cache).filter(file => file !== __filename).sort();
  const fingerprintCompiler = () => hash(JSON.stringify(compilerFiles.map(file => [file, hash(fs.readFileSync(file))])));
  const compilerHash = fingerprintCompiler();
  const observations = new Map();
  const original = ts.sys;
  const digest = value => hash(JSON.stringify(value === undefined ? null : value));
  const observe = (method, args, value) => {
    const key = JSON.stringify([method, args]);
    const fingerprint = digest(value);
    const previous = observations.get(key);
    if (previous && previous.hash !== fingerprint) throw new Error('Files changed during analysis. Retry.');
    observations.set(key, {method, args, hash: fingerprint});
    return value;
  };
  const sys = {...original};
  for (const method of ['readFile', 'fileExists', 'directoryExists', 'readDirectory', 'getDirectories', 'realpath']) {
    if (original[method]) sys[method] = (...args) => observe(method, args, original[method](...args));
  }
  function unchanged(manifest) {
    return manifest.every(item => {
      const fn = original[item.method];
      // JSON turns optional undefined array entries into null.
      const args = item.args.map(arg => arg === null ? undefined : arg);
      return fn && digest(fn(...args)) === item.hash;
    });
  }
  if (request.cache) {
    try {
      const cache = JSON.parse(fs.readFileSync(request.cache, 'utf8'));
      if (cache.root === root && cache.head === request.head && cache.compilerHash === compilerHash &&
          cache.manifest && unchanged(cache.manifest)) {
        progress('Using verified dependency cache');
        return {...cache, cacheHit: true};
      }
    } catch (_) { /* Missing, corrupt, or stale cache: build again. */ }
  }

  const tracked = new Set(request.files.map(file => path.resolve(root, file)));
  const relative = file => path.relative(root, path.resolve(file)).split(path.sep).join('/');
  const workspaceFile = file => {
    const resolved = path.resolve(file);
    if (!tracked.has(resolved) || resolved.includes(path.sep + 'node_modules' + path.sep)) return false;
    const real = sys.realpath(resolved);
    return real === root || real.startsWith(root + path.sep);
  };
  const supported = file => /\.(?:[cm]?tsx?|[cm]?jsx?)$/.test(file);
  const issues = new Map();
  let issueCount = 0;
  function issue(message) {
    if (issues.has(message)) return;
    issueCount++;
    if (issues.size < 200) issues.set(message, true);
  }
  const configs = new Map();
  const seenConfigs = new Set();
  function readConfig(config) {
    config = path.resolve(config);
    if (seenConfigs.has(config)) return;
    seenConfigs.add(config);
    if (!workspaceFile(config)) {
      issue('Project config is outside tracked workspace files: ' + relative(config));
      return;
    }
    const diagnostic = d => issue(relative(config) + ': ' + ts.flattenDiagnosticMessageText(d.messageText, ' '));
    const parsed = ts.getParsedCommandLineOfConfigFile(config, {}, {...sys, onUnRecoverableConfigFileDiagnostic: diagnostic});
    if (!parsed) return;
    for (const error of parsed.errors) diagnostic(error);
    configs.set(config, parsed);
    for (const ref of parsed.projectReferences || []) readConfig(ts.resolveProjectReferencePath(ref));
  }
  progress('Reading TypeScript project configurations');
  for (const file of tracked) {
    if (/^tsconfig(?:\.[^/]*)?\.json$/.test(path.basename(file))) readConfig(file);
  }
  if (!configs.size) throw new Error('No tracked tsconfig files found. Dependency analysis needs a TypeScript project.');

  const configuredFiles = new Set([...configs.values()].flatMap(config => config.fileNames.map(file => path.resolve(file))));
  const edges = new Map();
  const analysed = new Set();
  const evidenceSeen = new Set();
  let evidenceCount = 0;
  let externalImports = 0;
  let limitReached = false;
  const MAX_EVIDENCE = 150000;
  function add(sourceFile, targetFile, kind, node, declaration, symbol, symbolId) {
    if (!workspaceFile(targetFile.fileName)) return;
    const source = relative(sourceFile.fileName), target = relative(targetFile.fileName);
    // The overlay is between files. Local references do not need a file arrow.
    if (source === target) return;
    const location = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    const targetLocation = targetFile.getLineAndCharacterOfPosition(declaration ? declaration.getStart(targetFile) : 0);
    const evidence = {symbol, symbol_id: symbolId || null, line: location.line + 1,
      column: location.character + 1, target_line: targetLocation.line + 1, target_column: targetLocation.character + 1};
    const key = JSON.stringify([source, target, kind]);
    const evidenceKey = key + JSON.stringify(evidence);
    if (evidenceSeen.has(evidenceKey)) return;
    if (evidenceCount >= MAX_EVIDENCE) {
      limitReached = true;
      return;
    }
    evidenceSeen.add(evidenceKey);
    evidenceCount++;
    if (!edges.has(key)) edges.set(key, {source, target, kind, evidence: []});
    edges.get(key).evidence.push(evidence);
  }

  let projectIndex = 0;
  for (const [config, parsed] of configs) {
    progress(`Analysing project ${++projectIndex}/${configs.size}: ${relative(config)}`);
    if (!parsed.fileNames.length) continue;
    const snapshots = new Map();
    const host = {
      ...sys,
      useCaseSensitiveFileNames: () => sys.useCaseSensitiveFileNames,
      getCompilationSettings: () => parsed.options,
      getScriptFileNames: () => parsed.fileNames,
      getScriptVersion: () => '0',
      getScriptSnapshot: file => {
        if (!snapshots.has(file)) {
          const text = sys.readFile(file);
          snapshots.set(file, text === undefined ? undefined : ts.ScriptSnapshot.fromString(text));
        }
        return snapshots.get(file);
      },
      getCurrentDirectory: () => root,
      getDefaultLibFileName: options => ts.getDefaultLibFilePath(options),
      getProjectReferences: () => parsed.projectReferences,
      useSourceOfProjectReferenceRedirect: () => true,
    };
    const service = ts.createLanguageService(host);
    try {
      const program = service.getProgram();
      if (!program) { issue('Could not load project ' + relative(config)); continue; }
      const checker = program.getTypeChecker();
      const ownFiles = new Set(parsed.fileNames.map(file => path.resolve(file)));
      for (const diagnostic of [...program.getOptionsDiagnostics(), ...program.getGlobalDiagnostics()]) {
        if (diagnostic.category === ts.DiagnosticCategory.Error) {
          issue(relative(config) + ': ' + ts.flattenDiagnosticMessageText(diagnostic.messageText, ' '));
        }
      }
      const moduleCache = ts.createModuleResolutionCache(root, file => ts.sys.useCaseSensitiveFileNames ? file : file.toLowerCase(), parsed.options);
      function importLink(sourceFile, literal) {
        const mode = program.getModeForUsageLocation ? program.getModeForUsageLocation(sourceFile, literal) : undefined;
        const resolved = ts.resolveModuleName(literal.text, sourceFile.fileName, parsed.options, sys, moduleCache, undefined, mode).resolvedModule;
        if (!resolved) {
          issue(`${relative(sourceFile.fileName)}: unresolved import ${literal.text}`);
          return;
        }
        let target = program.getSourceFile(resolved.resolvedFileName);
        if (!target || !workspaceFile(target.fileName)) {
          // Project references may point at generated declarations. Ask the
          // language service for the source redirect before treating it as external.
          for (const definition of service.getDefinitionAtPosition(sourceFile.fileName, literal.getStart(sourceFile) + 1) || []) {
            if (workspaceFile(definition.fileName)) {
              target = program.getSourceFile(definition.fileName);
              if (target) break;
            }
          }
        }
        if (!target || !workspaceFile(target.fileName)) {
          externalImports++;
          if (!resolved.isExternalLibraryImport) issue(`${relative(sourceFile.fileName)}: import target outside analysed sources: ${literal.text}`);
          return;
        }
        add(sourceFile, target, 'import', literal, null, literal.text, null);
      }
      for (const sourceFile of program.getSourceFiles()) {
        if (!workspaceFile(sourceFile.fileName) || !supported(sourceFile.fileName)) continue;
        // Analyse each project's own files under its own compiler options, not
        // every transitive dependency again under the importing project's config.
        const absolute = path.resolve(sourceFile.fileName);
        if (configuredFiles.has(absolute) && !ownFiles.has(absolute)) continue;
        analysed.add(relative(sourceFile.fileName));
        for (const diagnostic of program.getSyntacticDiagnostics(sourceFile)) {
          if (diagnostic.category === ts.DiagnosticCategory.Error) {
            issue(relative(sourceFile.fileName) + ': ' + ts.flattenDiagnosticMessageText(diagnostic.messageText, ' '));
          }
        }
        function visit(node) {
          if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
            if (node.moduleSpecifier && ts.isStringLiteralLike(node.moduleSpecifier)) importLink(sourceFile, node.moduleSpecifier);
            return; // Represent bindings and re-exports as imports, not duplicate references.
          }
          if (ts.isImportEqualsDeclaration(node)) {
            const ref = node.moduleReference;
            if (ts.isExternalModuleReference(ref) && ref.expression && ts.isStringLiteralLike(ref.expression)) importLink(sourceFile, ref.expression);
            return;
          }
          if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteralLike(node.argument.literal)) {
            importLink(sourceFile, node.argument.literal);
          }
          const requireSymbol = ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'require'
            ? checker.getSymbolAtLocation(node.expression) : undefined;
          const localRequire = requireSymbol && (requireSymbol.declarations || []).some(declaration => workspaceFile(declaration.getSourceFile().fileName));
          if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
              (ts.isIdentifier(node.expression) && node.expression.text === 'require' && !localRequire))) {
            if (node.arguments.length === 1 && ts.isStringLiteralLike(node.arguments[0])) importLink(sourceFile, node.arguments[0]);
            else issue(relative(sourceFile.fileName) + ': non-literal dynamic import/require cannot be resolved');
          }
          const parent = node.parent;
          const propertyUse = parent && (ts.isPropertyAccessExpression(parent) || ts.isQualifiedName(parent) ||
            ts.isShorthandPropertyAssignment(parent) || ts.isJsxAttribute(parent));
          if (parent && (parent.label === node || ts.isMetaProperty(parent))) return;
          const literalUse = ts.isStringLiteralLike(node) && parent && ts.isElementAccessExpression(parent) && parent.argumentExpression === node;
          if ((ts.isIdentifier(node) || literalUse) && !(parent && parent.name === node && !propertyUse)) {
            let symbol = parent && ts.isShorthandPropertyAssignment(parent)
              ? checker.getShorthandAssignmentValueSymbol(parent) : checker.getSymbolAtLocation(node);
            if (symbol && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
            if (symbol) {
              const declaration = symbol.valueDeclaration || (symbol.declarations || [])[0];
              if (declaration) {
                const target = declaration.getSourceFile();
                const name = declaration.name || declaration;
                const symbolId = relative(target.fileName) + ':' + name.getStart(target);
                add(sourceFile, target, 'reference', node, name, symbol.getName(), symbolId);
              } else if (!['undefined', 'arguments'].includes(symbol.getName())) {
                issue(`${relative(sourceFile.fileName)}: no source definition for ${node.getText(sourceFile)}`);
              }
            } else {
              issue(`${relative(sourceFile.fileName)}: unresolved reference ${node.getText(sourceFile)}`);
            }
          }
          ts.forEachChild(node, visit);
        }
        visit(sourceFile);
      }
    } finally {
      service.dispose();
    }
    if (limitReached) { issue('Reference limit reached (150000 source locations). Results are incomplete.'); break; }
  }
  const uncovered = [...tracked].filter(file => supported(file) && !analysed.has(relative(file))).map(relative).sort();
  if (uncovered.length) issue(`${uncovered.length} tracked JS/TS files are outside loaded project coverage.`);
  const manifest = [...observations.values()];
  progress('Checking that source files and dependencies stayed unchanged');
  if (fingerprintCompiler() !== compilerHash || !unchanged(manifest)) {
    throw new Error('Source files or dependencies changed during analysis. Retry.');
  }
  return {
    root, head: request.head, typescript: ts.version, compilerHash, manifest, cacheHit: false,
    edges: [...edges.values()].sort((a, b) => JSON.stringify([a.source, a.target, a.kind]).localeCompare(JSON.stringify([b.source, b.target, b.kind]))),
    coverage: {projects: configs.size, files: analysed.size, analysedFiles: [...analysed].sort(),
      uncovered: uncovered.slice(0, 50), uncoveredCount: uncovered.length, externalImports,
      evidence: evidenceCount, complete: !issueCount, issueCount, issues: [...issues.keys()]},
  };
}

try {
  const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  process.stdout.write(JSON.stringify(main(request)));
} catch (error) {
  process.stderr.write('ERROR: ' + error.message + '\n');
  process.exitCode = 1;
}
