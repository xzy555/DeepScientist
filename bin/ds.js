#!/usr/bin/env node
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const repoRoot = path.resolve(__dirname, '..');
const srcPath = path.join(repoRoot, 'src');
const packageJson = JSON.parse(fs.readFileSync(path.join(repoRoot, 'package.json'), 'utf8'));
const pythonCandidates = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
const pythonCommands = new Set([
  'init',
  'new',
  'status',
  'pause',
  'resume',
  'daemon',
  'run',
  'note',
  'approve',
  'graph',
  'metrics',
  'push',
  'memory',
  'baseline',
  'config',
]);

const optionsWithValues = new Set(['--home', '--host', '--port', '--quest-id', '--mode']);

function printLauncherHelp() {
  console.log(`DeepScientist launcher

Usage:
  research
  research --web
  research --both
  research --stop
  research --restart
  research --home ~/DeepScientist --port 20999

Advanced Python CLI:
  ds init
  ds new "reproduce baseline and test idea"
  ds run decision --quest-id q-001 --message "review current state"
`);
}

function ensureDir(targetPath) {
  fs.mkdirSync(targetPath, { recursive: true });
}

function resolveHome(args) {
  const index = args.indexOf('--home');
  if (index >= 0 && index + 1 < args.length) {
    return path.resolve(args[index + 1]);
  }
  if (process.env.DEEPSCIENTIST_HOME) {
    return path.resolve(process.env.DEEPSCIENTIST_HOME);
  }
  return path.join(os.homedir(), 'DeepScientist');
}

function localUiUrl(host, port) {
  const resolvedHost = host && host !== '::' ? host : '0.0.0.0';
  return `http://${resolvedHost}:${port}`;
}

function parseLauncherArgs(argv) {
  const args = [...argv];
  let mode = 'both';
  let host = null;
  let port = null;
  let home = null;
  let stop = false;
  let restart = false;
  let openBrowser = false;
  let questId = null;
  let status = false;
  let daemonOnly = false;

  if (args[0] === 'ui') {
    args.shift();
  }

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === 'ui') continue;
    if (arg === '--web') mode = 'web';
    else if (arg === '--tui') mode = 'tui';
    else if (arg === '--both') mode = 'both';
    else if (arg === '--stop') stop = true;
    else if (arg === '--restart') restart = true;
    else if (arg === '--status') status = true;
    else if (arg === '--no-browser') openBrowser = false;
    else if (arg === '--open-browser') openBrowser = true;
    else if (arg === '--daemon-only') daemonOnly = true;
    else if (arg === '--host' && args[index + 1]) host = args[++index];
    else if (arg === '--port' && args[index + 1]) port = Number(args[++index]);
    else if (arg === '--home' && args[index + 1]) home = path.resolve(args[++index]);
    else if (arg === '--quest-id' && args[index + 1]) questId = args[++index];
    else if (arg === '--mode' && args[index + 1]) mode = args[++index];
    else if (arg === '--help' || arg === '-h') return { help: true };
    else if (!arg.startsWith('--')) return null;
  }

  return {
    help: false,
    mode,
    host,
    port,
    home,
    stop,
    restart,
    status,
    openBrowser,
    questId,
    daemonOnly,
  };
}

function findFirstPositionalArg(args) {
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (optionsWithValues.has(arg)) {
      index += 1;
      continue;
    }
    if (arg.startsWith('--')) {
      continue;
    }
    return { index, value: arg };
  }
  return null;
}

function resolveSystemPython() {
  for (const binary of pythonCandidates) {
    const result = spawnSync(binary, ['--version'], { stdio: 'ignore' });
    if (result.status === 0) {
      return binary;
    }
  }
  console.error('DeepScientist could not find a working Python 3 interpreter.');
  process.exit(1);
}

function venvPythonPath(home) {
  return process.platform === 'win32'
    ? path.join(home, 'runtime', 'venv', 'Scripts', 'python.exe')
    : path.join(home, 'runtime', 'venv', 'bin', 'python');
}

function venvRootPath(home) {
  return path.join(home, 'runtime', 'venv');
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function hashSkillTree() {
  const skillsRoot = path.join(repoRoot, 'src', 'skills');
  const hasher = crypto.createHash('sha256');
  if (!fs.existsSync(skillsRoot)) {
    hasher.update('missing');
    return hasher.digest('hex');
  }
  const stack = [skillsRoot];
  const files = [];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
        continue;
      }
      if (entry.isFile()) {
        files.push(fullPath);
      }
    }
  }
  files.sort();
  for (const filePath of files) {
    hasher.update(path.relative(skillsRoot, filePath));
    hasher.update(fs.readFileSync(filePath));
  }
  return hasher.digest('hex');
}

function discoverSkillIds() {
  const skillsRoot = path.join(repoRoot, 'src', 'skills');
  if (!fs.existsSync(skillsRoot)) {
    return [];
  }
  return fs
    .readdirSync(skillsRoot, { withFileTypes: true })
    .filter(
      (entry) =>
        entry.isDirectory() &&
        !entry.name.startsWith('.') &&
        fs.existsSync(path.join(skillsRoot, entry.name, 'SKILL.md'))
    )
    .map((entry) => entry.name)
    .sort();
}

function globalSkillsInstalled() {
  const skillIds = discoverSkillIds();
  const codexRoot = path.join(os.homedir(), '.codex', 'skills');
  const claudeRoot = path.join(os.homedir(), '.claude', 'agents');
  return skillIds.every((skillId) => {
    const codexSkill = path.join(codexRoot, `deepscientist-${skillId}`, 'SKILL.md');
    const claudeSkill = path.join(claudeRoot, `deepscientist-${skillId}.md`);
    return fs.existsSync(codexSkill) && fs.existsSync(claudeSkill);
  });
}

function runSync(binary, args, options = {}) {
  const result = spawnSync(binary, args, {
    cwd: repoRoot,
    stdio: options.capture ? 'pipe' : 'inherit',
    env: {
      ...process.env,
      PYTHONPATH: process.env.PYTHONPATH
        ? `${srcPath}${path.delimiter}${process.env.PYTHONPATH}`
        : srcPath,
    },
    encoding: 'utf8',
  });
  if (result.error) {
    throw result.error;
  }
  if (!options.allowFailure && result.status !== 0) {
    if (options.capture && result.stderr) {
      process.stderr.write(result.stderr);
    }
    process.exit(result.status ?? 1);
  }
  return result;
}

function step(index, total, message) {
  console.log(`[${index}/${total}] ${message}`);
}

function installPythonBundle(venvPython) {
  runSync(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel']);
  runSync(venvPython, ['-m', 'pip', 'install', '-e', repoRoot]);
}

function verifyPythonRuntime(venvPython) {
  const result = runSync(
    venvPython,
    ['-c', 'import deepscientist.cli; import cryptography; import _cffi_backend; print("ok")'],
    { capture: true, allowFailure: true }
  );
  return result.status === 0;
}

function recreatePythonRuntime(home, systemPython) {
  fs.rmSync(venvRootPath(home), { recursive: true, force: true });
  step(1, 4, 'Creating local Python runtime');
  runSync(systemPython, ['-m', 'venv', venvRootPath(home)]);
}

function ensurePythonRuntime(home) {
  ensureDir(path.join(home, 'runtime'));
  ensureDir(path.join(home, 'runtime', 'bundle'));
  const systemPython = resolveSystemPython();
  const stampPath = path.join(home, 'runtime', 'bundle', 'python-stamp.json');
  const desiredStamp = {
    version: packageJson.version,
    pyprojectHash: sha256File(path.join(repoRoot, 'pyproject.toml')),
  };

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const venvPython = venvPythonPath(home);
    if (!fs.existsSync(venvPython)) {
      recreatePythonRuntime(home, systemPython);
    }

    let currentStamp = null;
    if (fs.existsSync(stampPath)) {
      try {
        currentStamp = JSON.parse(fs.readFileSync(stampPath, 'utf8'));
      } catch {
        currentStamp = null;
      }
    }

    if (!currentStamp || currentStamp.version !== desiredStamp.version || currentStamp.pyprojectHash !== desiredStamp.pyprojectHash) {
      step(2, 4, 'Installing Python package and dependencies');
      installPythonBundle(venvPython);
      fs.writeFileSync(stampPath, `${JSON.stringify(desiredStamp, null, 2)}\n`, 'utf8');
    }

    if (verifyPythonRuntime(venvPython)) {
      return venvPython;
    }

    console.warn('DeepScientist is repairing the local Python runtime...');
    fs.rmSync(stampPath, { force: true });
    fs.rmSync(venvRootPath(home), { recursive: true, force: true });
  }

  console.error('DeepScientist could not prepare a healthy local Python runtime.');
  process.exit(1);
}

function runPythonCli(venvPython, args, options = {}) {
  return runSync(venvPython, ['-m', 'deepscientist.cli', ...args], options);
}

function normalizePythonCliArgs(args, home) {
  const normalized = [];
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--home') {
      index += 1;
      continue;
    }
    normalized.push(arg);
  }
  return ['--home', home, ...normalized];
}

function ensureInitialized(home, venvPython) {
  const stampPath = path.join(home, 'runtime', 'bundle', 'init-stamp.json');
  let currentStamp = null;
  if (fs.existsSync(stampPath)) {
    try {
      currentStamp = JSON.parse(fs.readFileSync(stampPath, 'utf8'));
    } catch {
      currentStamp = null;
    }
  }
  const desired = {
    version: packageJson.version,
    skills_hash: hashSkillTree(),
  };
  const configPath = path.join(home, 'config', 'config.yaml');
  if (
    currentStamp
    && currentStamp.version === desired.version
    && currentStamp.skills_hash === desired.skills_hash
    && fs.existsSync(configPath)
    && globalSkillsInstalled()
  ) {
    return;
  }
  step(3, 4, 'Preparing DeepScientist home, config, skills, and Git checks');
  const result = runPythonCli(venvPython, ['--home', home, 'init'], { capture: true, allowFailure: true });
  const stdout = result.stdout || '';
  let payload = {};
  try {
    payload = JSON.parse(stdout);
  } catch {
    payload = {};
  }
  if (payload.git && Array.isArray(payload.git.guidance) && payload.git.guidance.length > 0) {
    console.log('Git guidance:');
    for (const line of payload.git.guidance) {
      console.log(`  - ${line}`);
    }
  }
  if (payload.git && payload.git.installed === false) {
    console.error('Git is required before DeepScientist can run correctly.');
    process.exit(result.status || 1 || 1);
  }
  fs.writeFileSync(stampPath, `${JSON.stringify(desired, null, 2)}\n`, 'utf8');
}

function ensureNodeBundle(subdir, entryFile) {
  const fullEntry = path.join(repoRoot, subdir, entryFile);
  if (fs.existsSync(fullEntry)) {
    return fullEntry;
  }
  console.log(`Building ${subdir}...`);
  runSync('npm', ['--prefix', path.join(repoRoot, subdir), 'install']);
  runSync('npm', ['--prefix', path.join(repoRoot, subdir), 'run', 'build']);
  return fullEntry;
}

function daemonStatePath(home) {
  return path.join(home, 'runtime', 'daemon.json');
}

function normalizeHomePath(home) {
  try {
    return fs.realpathSync(home);
  } catch {
    return path.resolve(home);
  }
}

function readDaemonState(home) {
  const statePath = daemonStatePath(home);
  if (!fs.existsSync(statePath)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(statePath, 'utf8'));
  } catch {
    return null;
  }
}

function writeDaemonState(home, payload) {
  fs.writeFileSync(daemonStatePath(home), `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
}

function removeDaemonState(home) {
  const statePath = daemonStatePath(home);
  if (fs.existsSync(statePath)) {
    fs.unlinkSync(statePath);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function isHealthy(url) {
  const payload = await fetchHealth(url);
  return Boolean(payload && payload.status === 'ok');
}

async function fetchHealth(url) {
  try {
    const response = await fetch(`${url}/api/health`);
    if (!response.ok) {
      return null;
    }
    return await response.json();
  } catch {
    return null;
  }
}

function healthMatchesManagedState({ health, state, home }) {
  if (!health || health.status !== 'ok') {
    return false;
  }
  const expectedHome = normalizeHomePath(home);
  const actualHome = typeof health.home === 'string' && health.home ? normalizeHomePath(health.home) : null;
  if (!actualHome || actualHome !== expectedHome) {
    return false;
  }
  const expectedDaemonId = typeof state?.daemon_id === 'string' ? state.daemon_id.trim() : '';
  const actualDaemonId = typeof health.daemon_id === 'string' ? health.daemon_id.trim() : '';
  if (!expectedDaemonId || !actualDaemonId) {
    return false;
  }
  return expectedDaemonId === actualDaemonId;
}

function healthMatchesHome({ health, home }) {
  if (!health || health.status !== 'ok') {
    return false;
  }
  const expectedHome = normalizeHomePath(home);
  const actualHome = typeof health.home === 'string' && health.home ? normalizeHomePath(health.home) : null;
  return Boolean(actualHome && actualHome === expectedHome);
}

function daemonIdentityError({ url, home, health, state }) {
  const expectedHome = normalizeHomePath(home);
  const actualHome = typeof health?.home === 'string' ? health.home : 'unknown';
  const actualDaemonId = typeof health?.daemon_id === 'string' ? health.daemon_id : 'unknown';
  const expectedDaemonId = typeof state?.daemon_id === 'string' ? state.daemon_id : 'missing';
  return [
    `Refusing to operate on daemon at ${url} because its identity does not match this launcher state.`,
    `Expected home: ${expectedHome}`,
    `Reported home: ${actualHome}`,
    `Expected daemon_id: ${expectedDaemonId}`,
    `Reported daemon_id: ${actualDaemonId}`,
  ].join('\n');
}

async function requestDaemonShutdown(url, daemonId) {
  try {
    const response = await fetch(`${url}/api/admin/shutdown`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ source: 'ds-launcher', daemon_id: daemonId || null }),
    });
    if (!response.ok) {
      return false;
    }
    const payload = await response.json().catch(() => ({}));
    return payload.ok !== false;
  } catch {
    return false;
  }
}

function isPidAlive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function killManagedProcess(pid, signal) {
  if (!pid) return false;
  if (process.platform === 'win32') {
    const taskkillArgs = ['/PID', String(pid)];
    if (signal === 'SIGKILL') {
      taskkillArgs.push('/T', '/F');
    }
    const result = spawnSync('taskkill', taskkillArgs, { stdio: 'ignore' });
    return result.status === 0;
  }
  try {
    process.kill(-pid, signal);
    return true;
  } catch {
    try {
      process.kill(pid, signal);
      return true;
    } catch {
      return false;
    }
  }
}

async function waitForDaemonStop({ url, pid, attempts = 20, delayMs = 200 }) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const healthy = url ? await isHealthy(url) : false;
    const alive = pid ? isPidAlive(pid) : false;
    if (!healthy && !alive) {
      return true;
    }
    if (!healthy && !pid) {
      return true;
    }
    await sleep(delayMs);
  }
  return false;
}

function tailLog(logPath) {
  if (!fs.existsSync(logPath)) {
    return '';
  }
  const content = fs.readFileSync(logPath, 'utf8').trim();
  return content.split(/\r?\n/).slice(-20).join('\n');
}

async function stopDaemon(home) {
  const state = readDaemonState(home);
  const configured = readConfiguredUiAddressFromFile(home);
  const url = state?.url || localUiUrl(state?.host || configured.host, state?.port || configured.port);
  const healthBefore = await fetchHealth(url);
  const healthyBefore = Boolean(healthBefore && healthBefore.status === 'ok');
  const sameHomeHealthy = healthMatchesHome({ health: healthBefore, home });
  const pid = state?.pid || (sameHomeHealthy ? healthBefore?.pid : null);
  const shutdownDaemonId = sameHomeHealthy ? healthBefore?.daemon_id : state?.daemon_id;

  if (!state && !healthyBefore) {
    console.log('No managed DeepScientist daemon is running.');
    removeDaemonState(home);
    return;
  }

  if (!state && healthyBefore) {
    if (!sameHomeHealthy) {
      console.error(
        [
          `A DeepScientist daemon is reachable at ${url}, but there is no managed daemon state for ${normalizeHomePath(home)}.`,
          'Refusing to stop an unverified daemon.',
        ].join('\n')
      );
      process.exit(1);
    }
  }

  if (healthyBefore && !healthMatchesManagedState({ health: healthBefore, state, home })) {
    if (!sameHomeHealthy) {
      console.error(daemonIdentityError({ url, home, health: healthBefore, state }));
      process.exit(1);
    }
  }

  let stopped = false;

  if (healthyBefore) {
    await requestDaemonShutdown(url, shutdownDaemonId || null);
    stopped = await waitForDaemonStop({ url, pid, attempts: 20, delayMs: 200 });
  }

  if (!stopped && pid && isPidAlive(pid)) {
    killManagedProcess(pid, 'SIGTERM');
    stopped = await waitForDaemonStop({ url, pid, attempts: 30, delayMs: 200 });
  }

  if (!stopped && pid && isPidAlive(pid)) {
    killManagedProcess(pid, 'SIGKILL');
    stopped = await waitForDaemonStop({ url, pid, attempts: 20, delayMs: 150 });
  }

  const stillHealthy = await isHealthy(url);
  if (!stopped && (stillHealthy || (pid && isPidAlive(pid)))) {
    console.error('DeepScientist daemon is still running after shutdown attempts.');
    process.exit(1);
  }

  removeDaemonState(home);
  console.log('DeepScientist daemon stopped.');
}

async function readConfiguredUiAddress(home, venvPython, fallbackHost, fallbackPort) {
  try {
    const result = runPythonCli(venvPython, ['--home', home, 'config', 'show', 'config'], { capture: true, allowFailure: true });
    const text = result.stdout || '';
    const hostMatch = text.match(/^\s*host:\s*["']?([^"'\n]+)["']?\s*$/m);
    const portMatch = text.match(/^\s*port:\s*(\d+)\s*$/m);
    return {
      host: fallbackHost || (hostMatch ? hostMatch[1].trim() : '0.0.0.0'),
      port: fallbackPort || (portMatch ? Number(portMatch[1]) : 20999),
    };
  } catch {
    return { host: fallbackHost || '0.0.0.0', port: fallbackPort || 20999 };
  }
}

function readConfiguredUiAddressFromFile(home, fallbackHost, fallbackPort) {
  const configPath = path.join(home, 'config', 'config.yaml');
  if (!fs.existsSync(configPath)) {
    return { host: fallbackHost || '0.0.0.0', port: fallbackPort || 20999 };
  }
  try {
    const text = fs.readFileSync(configPath, 'utf8');
    const hostMatch = text.match(/^\s*host:\s*["']?([^"'\n]+)["']?\s*$/m);
    const portMatch = text.match(/^\s*port:\s*(\d+)\s*$/m);
    return {
      host: fallbackHost || (hostMatch ? hostMatch[1].trim() : '0.0.0.0'),
      port: fallbackPort || (portMatch ? Number(portMatch[1]) : 20999),
    };
  } catch {
    return { host: fallbackHost || '0.0.0.0', port: fallbackPort || 20999 };
  }
}

async function startDaemon(home, venvPython, host, port) {
  const bindUrl = `http://${host}:${port}`;
  const browserUrl = localUiUrl(host, port);
  const state = readDaemonState(home);
  const existingHealth = await fetchHealth(browserUrl);
  if (existingHealth && existingHealth.status === 'ok') {
    if (state && healthMatchesManagedState({ health: existingHealth, state, home })) {
      return { url: browserUrl, bindUrl, reused: true };
    }
    console.error(
      state
        ? daemonIdentityError({ url: browserUrl, home, health: existingHealth, state })
        : [
            `A DeepScientist daemon is already listening at ${browserUrl}, but it is not associated with the managed state for ${normalizeHomePath(home)}.`,
            'Use a different port or stop the foreign daemon first.',
          ].join('\n')
    );
    process.exit(1);
  }

  if (state && state.pid && !isPidAlive(state.pid)) {
    removeDaemonState(home);
  }

  ensureNodeBundle('src/ui', 'dist/index.html');

  const logPath = path.join(home, 'logs', 'daemon.log');
  ensureDir(path.dirname(logPath));
  const out = fs.openSync(logPath, 'a');
  const daemonId = crypto.randomUUID();
  const child = spawn(
    venvPython,
    ['-m', 'deepscientist.cli', '--home', home, 'daemon', '--host', host, '--port', String(port)],
    {
      cwd: repoRoot,
      detached: true,
      stdio: ['ignore', out, out],
      env: {
        ...process.env,
        DS_DAEMON_ID: daemonId,
        DS_DAEMON_MANAGED_BY: 'ds-launcher',
        PYTHONPATH: process.env.PYTHONPATH
          ? `${srcPath}${path.delimiter}${process.env.PYTHONPATH}`
          : srcPath,
      },
    }
  );
  child.unref();
  const statePayload = {
    pid: child.pid,
    host,
    port,
    url: browserUrl,
    bind_url: bindUrl,
    log_path: logPath,
    started_at: new Date().toISOString(),
    home: normalizeHomePath(home),
    daemon_id: daemonId,
  };
  writeDaemonState(home, statePayload);

  for (let attempt = 0; attempt < 60; attempt += 1) {
    const health = await fetchHealth(browserUrl);
    if (health && health.status === 'ok') {
      if (!healthMatchesManagedState({ health, state: readDaemonState(home), home })) {
        console.error(daemonIdentityError({ url: browserUrl, home, health, state: readDaemonState(home) }));
        process.exit(1);
      }
      return { url: browserUrl, bindUrl, reused: false };
    }
    await sleep(250);
  }

  console.error('DeepScientist daemon failed to become healthy.');
  const logTail = tailLog(logPath);
  if (logTail) {
    console.error(logTail);
  }
  process.exit(1);
}

function openBrowser(url) {
  if (process.platform === 'darwin') {
    spawn('open', [url], { detached: true, stdio: 'ignore' }).unref();
    return;
  }
  if (process.platform === 'win32') {
    spawn('cmd', ['/c', 'start', '', url], { detached: true, stdio: 'ignore' }).unref();
    return;
  }
  spawn('xdg-open', [url], { detached: true, stdio: 'ignore' }).unref();
}

function launchTui(url, questId) {
  const entry = ensureNodeBundle('src/tui', 'dist/index.js');
  const args = [entry, '--base-url', url];
  if (questId) {
    args.push('--quest-id', questId);
  }
  const child = spawn(process.execPath, args, {
    cwd: repoRoot,
    stdio: 'inherit',
  });
  child.on('exit', (code) => {
    process.exit(code ?? 0);
  });
}

async function launcherMain(rawArgs) {
  const options = parseLauncherArgs(rawArgs);
  if (!options) {
    return false;
  }
  if (options.help) {
    printLauncherHelp();
    process.exit(0);
  }

  const home = options.home || resolveHome(rawArgs);
  ensureDir(home);

  if (options.stop) {
    await stopDaemon(home);
    process.exit(0);
  }

  if (options.status) {
    const state = readDaemonState(home);
    const configured = readConfiguredUiAddressFromFile(home, options.host, options.port);
    const url = state?.url || localUiUrl(configured.host, configured.port);
    const health = await fetchHealth(url);
    const healthy = Boolean(health && health.status === 'ok');
    const identityMatch = state ? healthMatchesManagedState({ health, state, home }) : false;
    console.log(
      JSON.stringify(
        {
          healthy,
          identity_match: identityMatch,
          managed: Boolean(state),
          home,
          url,
          daemon: state,
          health,
        },
        null,
        2
      )
    );
    process.exit(healthy && (!state || identityMatch) ? 0 : 1);
  }

  const venvPython = ensurePythonRuntime(home);
  ensureInitialized(home, venvPython);

  const { host, port } = await readConfiguredUiAddress(home, venvPython, options.host, options.port);
  if (options.restart) {
    await stopDaemon(home);
  }

  step(4, 4, 'Starting local daemon and UI surfaces');
  const started = await startDaemon(home, venvPython, host, port);
  console.log('');
  console.log(`DeepScientist is ready at ${started.url}`);
  console.log('Use Ctrl+O in TUI to open the web workspace.');
  console.log('');

  if (options.mode === 'web' || options.mode === 'both' || options.openBrowser) {
    openBrowser(started.url);
  }
  if (options.daemonOnly) {
    process.exit(0);
  }
  if (options.mode === 'web') {
    process.exit(0);
  }
  launchTui(started.url, options.questId);
  return true;
}

async function main() {
  const args = process.argv.slice(2);
  const positional = findFirstPositionalArg(args);
  if (args.length === 0 || args[0] === 'ui' || (!positional && args[0]?.startsWith('--'))) {
    await launcherMain(args);
    return;
  }
  if (args[0] === '--help' || args[0] === '-h') {
    printLauncherHelp();
    return;
  }
  if (positional && pythonCommands.has(positional.value)) {
    const home = resolveHome(args);
    const venvPython = ensurePythonRuntime(home);
    const result = runPythonCli(venvPython, normalizePythonCliArgs(args, home), { allowFailure: true });
    process.exit(result.status ?? 0);
    return;
  }
  await launcherMain(args);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
