#!/usr/bin/env node

import fs, { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')
const lifecycleEvent = String(process.env.npm_lifecycle_event || '').trim()
const runningFromPrepack = lifecycleEvent === 'prepack'
const forceRebuild = ['1', 'true', 'yes', 'on'].includes(
  String(process.env.DEEPSCIENTIST_FORCE_REBUILD_BUNDLES || '')
    .trim()
    .toLowerCase()
)
const skipRebuild = ['1', 'true', 'yes', 'on'].includes(
  String(process.env.DEEPSCIENTIST_SKIP_BUNDLE_REBUILD || '')
    .trim()
    .toLowerCase()
)

function run(command, args, cwd = repoRoot) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: 'inherit',
    env: process.env,
  })
  if (result.error) {
    throw result.error
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1)
  }
}

function ensureFile(relativePath) {
  const fullPath = path.join(repoRoot, relativePath)
  if (!existsSync(fullPath)) {
    console.error(`Missing required release artifact: ${relativePath}`)
    process.exit(1)
  }
}

const webBundle = 'src/ui/dist/index.html'
const tuiBundle = 'src/tui/dist/index.js'

function latestMtimeForPaths(relativePaths) {
  let latest = 0
  const stack = relativePaths
    .map((relativePath) => path.join(repoRoot, relativePath))
    .filter((fullPath) => existsSync(fullPath))

  while (stack.length > 0) {
    const current = stack.pop()
    if (!current) continue
    const stats = fs.statSync(current)
    latest = Math.max(latest, stats.mtimeMs)
    if (!stats.isDirectory()) continue
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name === 'dist') continue
      stack.push(path.join(current, entry.name))
    }
  }

  return latest
}

function latestMtimeForTree(relativePath) {
  const fullPath = path.join(repoRoot, relativePath)
  if (!existsSync(fullPath)) return 0
  let latest = 0
  const stack = [fullPath]
  while (stack.length > 0) {
    const current = stack.pop()
    if (!current) continue
    const stats = fs.statSync(current)
    latest = Math.max(latest, stats.mtimeMs)
    if (!stats.isDirectory()) continue
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      stack.push(path.join(current, entry.name))
    }
  }
  return latest
}

function bundleFreshness() {
  const webSourceMtime = latestMtimeForPaths([
    'src/ui/src',
    'src/ui/public',
    'src/ui/package.json',
    'src/ui/package-lock.json',
    'src/ui/postcss.config.cjs',
    'src/ui/tailwind.config.ts',
    'src/ui/tsconfig.json',
    'src/ui/vite.config.ts',
    'src/ui/index.html',
  ])
  const tuiSourceMtime = latestMtimeForPaths([
    'src/tui/src',
    'src/tui/package.json',
    'src/tui/package-lock.json',
    'src/tui/tsconfig.json',
  ])
  const webDistMtime = latestMtimeForTree('src/ui/dist')
  const tuiDistMtime = latestMtimeForTree('src/tui/dist')
  return {
    webFresh: webDistMtime >= webSourceMtime && webDistMtime > 0,
    tuiFresh: tuiDistMtime >= tuiSourceMtime && tuiDistMtime > 0,
  }
}

const freshness = bundleFreshness()
const bundlesFresh = freshness.webFresh && freshness.tuiFresh

if (runningFromPrepack && !forceRebuild) {
  ensureFile(webBundle)
  ensureFile(tuiBundle)
  if (!bundlesFresh) {
    console.error('Prebuilt UI/TUI bundles are stale for npm pack/publish.')
    console.error('Run `npm run build:release` first, then rerun `npm pack` or `npm publish`.')
    process.exit(1)
  }
  process.exit(0)
}

if (!skipRebuild || forceRebuild) {
  run('npm', ['--prefix', 'src/ui', 'ci', '--include=dev', '--no-audit', '--no-fund'])
  run('npm', ['--prefix', 'src/ui', 'run', 'build'])

  run('npm', ['--prefix', 'src/tui', 'ci', '--include=dev', '--no-audit', '--no-fund'])
  run('npm', ['--prefix', 'src/tui', 'run', 'build'])
}

ensureFile(webBundle)
ensureFile(tuiBundle)
